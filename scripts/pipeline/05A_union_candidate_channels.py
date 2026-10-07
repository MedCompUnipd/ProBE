#!/usr/bin/env python3
"""
Production ProBE candidate pipeline.

Stage 05A: lossless union of the two independent candidate-discovery channels:

  Stage 03  -> direct target-sequence discovery (k=7, taxonomy constrained)
  Stage 04E -> direct/temporal lineage discovery (frozen one-hop metadata keys)

The unit of union is:
    (release, target_id, UniProt RID)

This stage makes NO SAME_ENTITY decision, performs NO candidate filtering and
does NOT align sequences. It only merges facts, verifies overlapping record
metadata, and labels provenance as:

    SEQUENCE_ONLY
    LINEAGE_ONLY
    BOTH

Important:
- Stage 03 record_number is the ordinal in the GO-positive filtered FASTA.
- Stage 04E record_number is the ordinal in the full V2 FASTA.
  These are intentionally preserved as separate columns and never compared.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

SEQ_FACT_MAP = {
    "target_primary_bucket": "target_bucket",
    "candidate_primary_accession": "accession",
    "entry": "entry",
    "rid": "rid",
    "raw_ox": "raw_ox",
    "resolved_taxid": "resolved_taxid",
    "species_taxid": "species_taxid",
    "frag": "frag",
    "sequence_length": "candidate_length",
    "pe": "pe",
    "sv": "sv",
    "sec": "sec",
    "gene": "gene",
}

LIN_FACT_MAP = {
    "target_primary_bucket": "target_primary_bucket",
    "candidate_primary_accession": "candidate_primary_accession",
    "entry": "entry",
    "rid": "rid",
    "raw_ox": "raw_ox",
    "resolved_taxid": "resolved_taxid",
    "species_taxid": "species_taxid",
    "frag": "frag",
    "sequence_length": "sequence_length",
    "pe": "pe",
    "sv": "sv",
    "sec": "sec",
    "gene": "gene",
}

SEQ_EVIDENCE_FIELDS = [
    "target_length",
    "candidate_length",
    "length_ratio",
    "candidate_over_target_length",
    "target_distinct_kmers",
    "shared_distinct_kmers",
    "target_shared_fraction",
    "exact_sequence",
    "shared_unique_positional_kmers",
    "target_seed_span_fraction",
    "candidate_seed_span_fraction",
    "target_seed_residue_coverage",
    "candidate_seed_residue_coverage",
    "lis_count",
    "lis_fraction",
    "diagonal_min",
    "diagonal_max",
    "diagonal_range",
]

LIN_EVIDENCE_FIELDS = [
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


@dataclass
class Candidate:
    release: str
    target_id: str
    rid: str

    sequence_discovered: bool = False
    lineage_discovered: bool = False

    sequence_search_record_number: str = ""
    full_v2_record_number: str = ""

    facts: dict[str, str] = field(default_factory=dict)
    seq: dict[str, str] = field(default_factory=dict)
    lin: dict[str, str] = field(default_factory=dict)


def required_columns_for_sequence() -> set[str]:
    return {
        "record_number",
        "target_id",
        *SEQ_FACT_MAP.values(),
        *SEQ_EVIDENCE_FIELDS,
    }


def required_columns_for_lineage() -> set[str]:
    return {
        "record_number",
        "target_id",
        *LIN_FACT_MAP.values(),
        *LIN_EVIDENCE_FIELDS,
    }


def check_header(
    reader: csv.DictReader,
    required: set[str],
    path: Path,
) -> None:
    if reader.fieldnames is None or not required.issubset(reader.fieldnames):
        missing = required.difference(reader.fieldnames or ())
        raise ValueError(f"{path}: missing columns {sorted(missing)}")


def merge_fact(
    candidate: Candidate,
    name: str,
    value: str,
    source: str,
) -> None:
    previous = candidate.facts.get(name)
    if previous is None:
        candidate.facts[name] = value
        return
    if previous != value:
        raise ValueError(
            "record metadata conflict for "
            f"release={candidate.release} target={candidate.target_id} "
            f"RID={candidate.rid}: field={name} "
            f"{previous!r} != {value!r} from {source}"
        )


def load_sequence(
    path: Path,
    release: str,
    candidates: dict[tuple[str, str, str], Candidate],
) -> int:
    rows = 0
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        check_header(reader, required_columns_for_sequence(), path)

        seen: set[tuple[str, str]] = set()
        for row in reader:
            rows += 1
            pair_key = (row["target_id"], row["rid"])
            if pair_key in seen:
                raise ValueError(
                    f"{path}: duplicate Stage-03 target/RID pair {pair_key}"
                )
            seen.add(pair_key)

            key = (release, row["target_id"], row["rid"])
            candidate = candidates.get(key)
            if candidate is None:
                candidate = Candidate(release, row["target_id"], row["rid"])
                candidates[key] = candidate

            if candidate.sequence_discovered:
                raise ValueError(f"{path}: duplicate sequence candidate {key}")

            candidate.sequence_discovered = True
            candidate.sequence_search_record_number = row["record_number"]

            for out_name, in_name in SEQ_FACT_MAP.items():
                merge_fact(candidate, out_name, row[in_name], "Stage03")

            candidate.seq = {
                name: row[name] for name in SEQ_EVIDENCE_FIELDS
            }

    return rows


def load_lineage(
    path: Path,
    expected_release: str,
    candidates: dict[tuple[str, str, str], Candidate],
) -> int:
    rows = 0
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        check_header(reader, required_columns_for_lineage(), path)

        seen: set[tuple[str, str]] = set()
        for row in reader:
            rows += 1
            if row["release"] != expected_release:
                raise ValueError(
                    f"{path}: expected release {expected_release}, "
                    f"found {row['release']}"
                )

            pair_key = (row["target_id"], row["rid"])
            if pair_key in seen:
                raise ValueError(
                    f"{path}: duplicate Stage-04E target/RID pair {pair_key}"
                )
            seen.add(pair_key)

            key = (expected_release, row["target_id"], row["rid"])
            candidate = candidates.get(key)
            if candidate is None:
                candidate = Candidate(
                    expected_release, row["target_id"], row["rid"]
                )
                candidates[key] = candidate

            if candidate.lineage_discovered:
                raise ValueError(f"{path}: duplicate lineage candidate {key}")

            candidate.lineage_discovered = True
            candidate.full_v2_record_number = row["record_number"]

            for out_name, in_name in LIN_FACT_MAP.items():
                merge_fact(candidate, out_name, row[in_name], "Stage04E")

            candidate.lin = {
                name: row[name] for name in LIN_EVIDENCE_FIELDS
            }

    return rows


def channel(c: Candidate) -> str:
    if c.sequence_discovered and c.lineage_discovered:
        return "BOTH"
    if c.sequence_discovered:
        return "SEQUENCE_ONLY"
    if c.lineage_discovered:
        return "LINEAGE_ONLY"
    raise AssertionError("candidate has no discovery channel")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start-sequence", type=Path, required=True)
    ap.add_argument("--end-sequence", type=Path, required=True)
    ap.add_argument("--start-lineage", type=Path, required=True)
    ap.add_argument("--end-lineage", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--summary", type=Path, required=True)
    ap.add_argument("--start-release-id", required=True)
    ap.add_argument("--end-release-id", required=True)
    args = ap.parse_args()

    for path in (
        args.start_sequence,
        args.end_sequence,
        args.start_lineage,
        args.end_lineage,
    ):
        if not path.is_file():
            raise FileNotFoundError(path)

    for path in (args.output, args.summary):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)

    candidates: dict[tuple[str, str, str], Candidate] = {}

    input_counts = {
        "start_sequence_rows": load_sequence(
            args.start_sequence, args.start_release_id, candidates
        ),
        "end_sequence_rows": load_sequence(
            args.end_sequence, args.end_release_id, candidates
        ),
        "start_lineage_rows": load_lineage(
            args.start_lineage, args.start_release_id, candidates
        ),
        "end_lineage_rows": load_lineage(
            args.end_lineage, args.end_release_id, candidates
        ),
    }

    rows = sorted(
        candidates.values(),
        key=lambda c: (c.release, c.target_id, int(c.rid)),
    )

    base_fields = [
        "release",
        "target_id",
        "rid",
        "discovery_channel",
        "sequence_discovered",
        "lineage_discovered",
        "sequence_search_record_number",
        "full_v2_record_number",
        "target_primary_bucket",
        "candidate_primary_accession",
        "entry",
        "raw_ox",
        "resolved_taxid",
        "species_taxid",
        "frag",
        "sequence_length",
        "pe",
        "sv",
        "sec",
        "gene",
    ]

    output_fields = base_fields + [
        f"seq_{name}" for name in SEQ_EVIDENCE_FIELDS
    ] + [
        f"lin_{name}" for name in LIN_EVIDENCE_FIELDS
    ]

    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=output_fields,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()

        for c in rows:
            row = {
                "release": c.release,
                "target_id": c.target_id,
                "rid": c.rid,
                "discovery_channel": channel(c),
                "sequence_discovered": int(c.sequence_discovered),
                "lineage_discovered": int(c.lineage_discovered),
                "sequence_search_record_number":
                    c.sequence_search_record_number,
                "full_v2_record_number": c.full_v2_record_number,
                **c.facts,
            }
            for name in SEQ_EVIDENCE_FIELDS:
                row[f"seq_{name}"] = c.seq.get(name, "")
            for name in LIN_EVIDENCE_FIELDS:
                row[f"lin_{name}"] = c.lin.get(name, "")
            writer.writerow(row)

    channel_counts = Counter(channel(c) for c in rows)
    release_channel_counts: dict[str, Counter[str]] = {
        args.start_release_id: Counter(),
        args.end_release_id: Counter(),
    }
    targets_by_release: dict[str, set[str]] = {
        args.start_release_id: set(),
        args.end_release_id: set(),
    }
    for c in rows:
        release_channel_counts[c.release][channel(c)] += 1
        targets_by_release[c.release].add(c.target_id)

    both = channel_counts["BOTH"]
    expected_union = (
        input_counts["start_sequence_rows"]
        + input_counts["end_sequence_rows"]
        + input_counts["start_lineage_rows"]
        + input_counts["end_lineage_rows"]
        - both
    )
    if expected_union != len(rows):
        raise AssertionError(
            f"union arithmetic failed: expected {expected_union}, got {len(rows)}"
        )

    summary = {
        "stage": "05A",
        "input_counts": input_counts,
        "candidate_union_row_count": len(rows),
        "channel_counts": dict(sorted(channel_counts.items())),
        "release_channel_counts": {
            release: dict(sorted(counts.items()))
            for release, counts in release_channel_counts.items()
        },
        "targets_with_any_candidate_by_release": {
            release: len(targets)
            for release, targets in targets_by_release.items()
        },
        "metadata_conflicts_detected": 0,
        "policy": {
            "union_key": ["release", "target_id", "rid"],
            "same_entity_decision_made_here": False,
            "candidate_filtering_made_here": False,
            "alignment_made_here": False,
            "sequence_and_lineage_channels_remain_explicit": True,
            "stage03_record_number_semantics":
                "GO-positive filtered FASTA ordinal",
            "stage04_record_number_semantics":
                "full V2 FASTA ordinal",
        },
    }

    with args.summary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)
        handle.write("\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
