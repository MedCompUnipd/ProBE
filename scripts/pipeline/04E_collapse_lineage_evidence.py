#!/usr/bin/env python3
"""
Production ProBE candidate pipeline.

Stage 04E: collapse Stage 04D2 long-form lineage evidence to one row per
(release, target_id, candidate RID).

No SAME_ENTITY decision and no filtering are performed here.
The original 04D2 long-form files remain the authoritative provenance.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

FACT_FIELDS = (
    "release",
    "target_id",
    "target_primary_bucket",
    "record_number",
    "candidate_primary_accession",
    "entry",
    "rid",
    "raw_ox",
    "resolved_taxid",
    "species_taxid",
    "frag",
    "sequence_length",
    "pe",
    "sv",
    "sec",
    "gene",
)


@dataclass
class Candidate:
    release: str
    target_id: str
    target_primary_bucket: str
    record_number: str
    candidate_primary_accession: str
    entry: str
    rid: str
    raw_ox: str
    resolved_taxid: str
    species_taxid: str
    frag: str
    sequence_length: str
    pe: str
    sv: str
    sec: str
    gene: str

    evidence_rows: int = 0
    typed_keys: set[tuple[str, str]] = field(default_factory=set)
    record_roles: set[str] = field(default_factory=set)
    source_roles: set[str] = field(default_factory=set)

    accession_primary_keys: set[str] = field(default_factory=set)
    accession_secondary_keys: set[str] = field(default_factory=set)
    ordered_locus_keys: set[str] = field(default_factory=set)
    orf_keys: set[str] = field(default_factory=set)

    target_accession_keys: set[tuple[str, str]] = field(default_factory=set)
    start_anchor_keys: set[tuple[str, str]] = field(default_factory=set)
    end_anchor_keys: set[tuple[str, str]] = field(default_factory=set)
    collision_keys: set[tuple[str, str]] = field(default_factory=set)

    max_key_target_multiplicity: int = 0


def make_candidate(row: dict[str, str]) -> Candidate:
    return Candidate(**{name: row[name] for name in FACT_FIELDS})


def validate_same_candidate(c: Candidate, row: dict[str, str]) -> None:
    for name in FACT_FIELDS:
        old = getattr(c, name)
        new = row[name]
        if old != new:
            raise ValueError(
                "inconsistent 04D2 record facts for "
                f"release={c.release} target={c.target_id} RID={c.rid}: "
                f"{name}: {old!r} != {new!r}"
            )


def add_evidence(c: Candidate, row: dict[str, str]) -> None:
    c.evidence_rows += 1

    key_type = row["key_type"]
    key_value = row["key_value"]
    typed_key = (key_type, key_value)
    c.typed_keys.add(typed_key)

    role = row["record_match_role"]
    c.record_roles.add(role)

    if role == "ACCESSION_PRIMARY":
        c.accession_primary_keys.add(key_value)
    elif role == "ACCESSION_SECONDARY":
        c.accession_secondary_keys.add(key_value)
    elif role == "ORDERED_LOCUS":
        c.ordered_locus_keys.add(key_value)
    elif role == "ORF":
        c.orf_keys.add(key_value)
    else:
        raise ValueError(f"unknown record_match_role: {role}")

    for source_role in row["source_roles"].split(","):
        if source_role:
            c.source_roles.add(source_role)

    if row["from_target_accession"] == "1":
        c.target_accession_keys.add(typed_key)
    if row["from_start_anchor"] == "1":
        c.start_anchor_keys.add(typed_key)
    if row["from_end_anchor"] == "1":
        c.end_anchor_keys.add(typed_key)
    if row["key_collision"] == "1":
        c.collision_keys.add(typed_key)

    multiplicity = int(row["key_target_multiplicity"])
    if multiplicity < 1:
        raise ValueError("key_target_multiplicity must be >= 1")
    c.max_key_target_multiplicity = max(
        c.max_key_target_multiplicity, multiplicity
    )


def encode_values(values: set[str]) -> str:
    return ",".join(sorted(values))


def encode_typed_keys(values: set[tuple[str, str]]) -> str:
    return ",".join(
        f"{key_type}:{key_value}"
        for key_type, key_value in sorted(values)
    )


def lineage_fact_signature(c: Candidate) -> str:
    flags: list[str] = []
    if c.target_accession_keys:
        flags.append("TARGET_ACCESSION")
    if c.accession_primary_keys:
        flags.append("ACCESSION_PRIMARY")
    if c.accession_secondary_keys:
        flags.append("ACCESSION_SECONDARY")
    if c.ordered_locus_keys:
        flags.append("ORDERED_LOCUS")
    if c.orf_keys:
        flags.append("ORF")
    if c.start_anchor_keys:
        flags.append("START_ANCHOR_DERIVED")
    if c.end_anchor_keys:
        flags.append("END_ANCHOR_DERIVED")
    if c.collision_keys:
        flags.append("KEY_COLLISION_PRESENT")
    return ",".join(flags)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--summary", type=Path, required=True)
    args = ap.parse_args()

    if not args.input.is_file():
        raise FileNotFoundError(args.input)

    for path in (args.output, args.summary):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)

    candidates: dict[tuple[str, str, str], Candidate] = {}
    input_rows = 0

    with args.input.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        required = {
            *FACT_FIELDS,
            "key_type",
            "key_value",
            "record_match_role",
            "key_target_multiplicity",
            "key_collision",
            "from_target_accession",
            "from_start_anchor",
            "from_end_anchor",
            "source_roles",
        }
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            missing = required.difference(reader.fieldnames or ())
            raise ValueError(f"{args.input}: missing columns {sorted(missing)}")

        for row in reader:
            input_rows += 1
            key = (row["release"], row["target_id"], row["rid"])
            c = candidates.get(key)
            if c is None:
                c = make_candidate(row)
                candidates[key] = c
            else:
                validate_same_candidate(c, row)
            add_evidence(c, row)

    rows = sorted(
        candidates.values(),
        key=lambda c: (c.release, c.target_id, int(c.rid)),
    )

    fields = [
        *FACT_FIELDS,
        "lineage_evidence_row_count",
        "distinct_lineage_key_count",
        "lineage_fact_signature",
        "record_match_roles",
        "source_roles",
        "target_accession_match",
        "from_start_anchor",
        "from_end_anchor",
        "has_accession_primary_match",
        "has_accession_secondary_match",
        "has_ordered_locus_match",
        "has_orf_match",
        "key_collision_present",
        "collision_key_count",
        "max_key_target_multiplicity",
        "matched_typed_keys",
        "target_accession_keys",
        "accession_primary_keys",
        "accession_secondary_keys",
        "ordered_locus_keys",
        "orf_keys",
        "collision_keys",
    ]

    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fields,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()

        for c in rows:
            writer.writerow(
                {
                    **{name: getattr(c, name) for name in FACT_FIELDS},
                    "lineage_evidence_row_count": c.evidence_rows,
                    "distinct_lineage_key_count": len(c.typed_keys),
                    "lineage_fact_signature": lineage_fact_signature(c),
                    "record_match_roles": encode_values(c.record_roles),
                    "source_roles": encode_values(c.source_roles),
                    "target_accession_match":
                        int(bool(c.target_accession_keys)),
                    "from_start_anchor": int(bool(c.start_anchor_keys)),
                    "from_end_anchor": int(bool(c.end_anchor_keys)),
                    "has_accession_primary_match":
                        int(bool(c.accession_primary_keys)),
                    "has_accession_secondary_match":
                        int(bool(c.accession_secondary_keys)),
                    "has_ordered_locus_match":
                        int(bool(c.ordered_locus_keys)),
                    "has_orf_match": int(bool(c.orf_keys)),
                    "key_collision_present":
                        int(bool(c.collision_keys)),
                    "collision_key_count": len(c.collision_keys),
                    "max_key_target_multiplicity":
                        c.max_key_target_multiplicity,
                    "matched_typed_keys": encode_typed_keys(c.typed_keys),
                    "target_accession_keys":
                        encode_typed_keys(c.target_accession_keys),
                    "accession_primary_keys":
                        encode_values(c.accession_primary_keys),
                    "accession_secondary_keys":
                        encode_values(c.accession_secondary_keys),
                    "ordered_locus_keys":
                        encode_values(c.ordered_locus_keys),
                    "orf_keys": encode_values(c.orf_keys),
                    "collision_keys":
                        encode_typed_keys(c.collision_keys),
                }
            )

    summary = {
        "stage": "04E",
        "input_evidence_row_count": input_rows,
        "reconstructed_evidence_row_count":
            sum(c.evidence_rows for c in rows),
        "collapsed_target_record_pair_count": len(rows),
        "targets_with_collapsed_lineage_candidate":
            len({c.target_id for c in rows}),
        "fragment_target_record_pair_count":
            sum(c.frag == "1" for c in rows),
        "collision_target_record_pair_count":
            sum(bool(c.collision_keys) for c in rows),
        "target_accession_match_pair_count":
            sum(bool(c.target_accession_keys) for c in rows),
        "start_anchor_derived_pair_count":
            sum(bool(c.start_anchor_keys) for c in rows),
        "end_anchor_derived_pair_count":
            sum(bool(c.end_anchor_keys) for c in rows),
        "lineage_fact_signature_counts": dict(
            sorted(Counter(lineage_fact_signature(c) for c in rows).items())
        ),
        "policy": {
            "same_entity_decision_made_here": False,
            "candidate_filtering_made_here": False,
            "long_form_04D2_remains_authoritative_provenance": True,
            "one_output_row_per_release_target_rid": True,
        },
    }

    with args.summary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)
        handle.write("\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
