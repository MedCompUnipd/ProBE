#!/usr/bin/env python3
"""
ProBE production Stage 04C

Stage 04C: compile the frozen one-hop lineage key universe.

IMPORTANT POLICY
----------------
Lineage keys are generated ONLY from:
  1) the canonical target optional accession;
  2) taxonomically eligible DIRECT ACCESSION ANCHORS classified by Stage 04B.

Stage 03 sequence candidates are deliberately NOT lineage-key sources.  They
remain independent sequence evidence and will be united with lineage evidence
later.  This prevents a sequence-similar paralog from donating its own SEC,
OrderedLocusName or ORF identifiers to the target.

Candidate-generating key types:
  ACCESSION
  ORDERED_LOCUS  (GENE L:)
  ORF            (GENE O:)

GENE N/S, PE, SV and GO never generate keys.

Keys are frozen in this file.  Records found by the subsequent lineage scan
must never contribute additional keys.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Evidence:
    from_target_accession: bool = False
    from_start_anchor: bool = False
    from_end_anchor: bool = False
    source_match_types: set[str] = field(default_factory=set)
    source_primary_accessions: set[str] = field(default_factory=set)
    source_rids: set[str] = field(default_factory=set)
    source_frag_flags: set[str] = field(default_factory=set)


def split_csv(value: str) -> list[str]:
    return [x for x in value.split(",") if x] if value else []


def parse_gene(gene: str) -> dict[str, set[str]]:
    result = {"N": set(), "S": set(), "L": set(), "O": set()}
    if not gene:
        return result
    for group in gene.split("|"):
        for token in group.split(";"):
            if len(token) < 3 or token[1] != ":" or token[0] not in result:
                continue
            for value in token[2:].split(","):
                if value:
                    result[token[0]].add(value)
    return result


def load_targets(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        required = {"target_id", "optional_accession"}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError("target TSV lacks target_id/optional_accession")
        for row in reader:
            tid = row["target_id"]
            if tid in result:
                raise ValueError(f"duplicate target_id: {tid}")
            result[tid] = row["optional_accession"]
    return result


def add_key(
    keys: dict[tuple[str, str, str], Evidence],
    *,
    target_id: str,
    key_type: str,
    key_value: str,
    target_accession: bool = False,
    anchor_role: str = "",
    anchor_match_type: str = "",
    anchor_primary: str = "",
    anchor_rid: str = "",
    anchor_frag: str = "",
) -> None:
    if not key_value:
        return

    ev = keys.setdefault((target_id, key_type, key_value), Evidence())
    ev.from_target_accession |= target_accession

    if anchor_role == "START":
        ev.from_start_anchor = True
    elif anchor_role == "END":
        ev.from_end_anchor = True
    elif anchor_role:
        raise ValueError(f"unexpected temporal role: {anchor_role}")

    if anchor_match_type:
        ev.source_match_types.add(anchor_match_type)
    if anchor_primary:
        ev.source_primary_accessions.add(anchor_primary)
    if anchor_rid:
        ev.source_rids.add(anchor_rid)
    if anchor_frag:
        ev.source_frag_flags.add(anchor_frag)


def consume_classified_anchors(
    path: Path,
    keys: dict[tuple[str, str, str], Evidence],
    *,
    role: str,
    expected_release: str,
) -> tuple[int, int]:
    if role not in {"START", "END"}:
        raise ValueError(f"unexpected temporal role: {role}")

    total = 0
    eligible = 0

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        required = {
            "release",
            "target_id",
            "direct_match_type",
            "primary_accession",
            "rid",
            "frag",
            "sec",
            "gene",
            "key_source_eligible",
        }
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            missing = required.difference(reader.fieldnames or ())
            raise ValueError(f"{path}: missing columns {sorted(missing)}")

        for row in reader:
            if row["release"] != expected_release:
                raise ValueError(
                    f"{path}: expected release {expected_release}, "
                    f"found {row['release']}"
                )

            total += 1
            if row["key_source_eligible"] != "1":
                continue
            eligible += 1

            common = {
                "target_id": row["target_id"],
                "anchor_role": role,
                "anchor_match_type": row["direct_match_type"],
                "anchor_primary": row["primary_accession"],
                "anchor_rid": row["rid"],
                "anchor_frag": row["frag"],
            }

            add_key(
                keys,
                key_type="ACCESSION",
                key_value=row["primary_accession"],
                **common,
            )
            for sec in split_csv(row["sec"]):
                add_key(
                    keys,
                    key_type="ACCESSION",
                    key_value=sec,
                    **common,
                )

            genes = parse_gene(row["gene"])
            for value in genes["L"]:
                add_key(
                    keys,
                    key_type="ORDERED_LOCUS",
                    key_value=value,
                    **common,
                )
            for value in genes["O"]:
                add_key(
                    keys,
                    key_type="ORF",
                    key_value=value,
                    **common,
                )

    return total, eligible


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", type=Path, required=True)
    ap.add_argument("--start-anchors", type=Path, required=True)
    ap.add_argument("--end-anchors", type=Path, required=True)
    ap.add_argument("--start-release-id", required=True)
    ap.add_argument("--end-release-id", required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--summary", type=Path, required=True)
    args = ap.parse_args()

    for path in (args.targets, args.start_anchors, args.end_anchors):
        if not path.is_file():
            raise FileNotFoundError(path)

    for path in (args.output, args.summary):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)

    targets = load_targets(args.targets)
    keys: dict[tuple[str, str, str], Evidence] = {}

    # Original target accession is always a frozen accession key.
    for tid, accession in targets.items():
        if accession:
            add_key(
                keys,
                target_id=tid,
                key_type="ACCESSION",
                key_value=accession,
                target_accession=True,
            )

    start_total, start_eligible = consume_classified_anchors(
        args.start_anchors,
        keys,
        role="START",
        expected_release=args.start_release_id,
    )
    end_total, end_eligible = consume_classified_anchors(
        args.end_anchors,
        keys,
        role="END",
        expected_release=args.end_release_id,
    )

    rows = sorted(keys.items(), key=lambda kv: kv[0])
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        fields = [
            "target_id",
            "key_type",
            "key_value",
            "from_target_accession",
            "from_start_anchor",
            "from_end_anchor",
            "source_match_types",
            "source_primary_accessions",
            "source_rids",
            "source_frag_flags",
        ]
        writer = csv.DictWriter(
            handle,
            fieldnames=fields,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()

        for (tid, key_type, key_value), ev in rows:
            writer.writerow(
                {
                    "target_id": tid,
                    "key_type": key_type,
                    "key_value": key_value,
                    "from_target_accession": int(ev.from_target_accession),
                    "from_start_anchor": int(ev.from_start_anchor),
                    "from_end_anchor": int(ev.from_end_anchor),
                    "source_match_types": ",".join(sorted(ev.source_match_types)),
                    "source_primary_accessions": ",".join(
                        sorted(ev.source_primary_accessions)
                    ),
                    "source_rids": ",".join(
                        sorted(ev.source_rids, key=lambda x: int(x))
                    ),
                    "source_frag_flags": ",".join(
                        sorted(ev.source_frag_flags)
                    ),
                }
            )

    by_type = Counter(key_type for (_tid, key_type, _value), _ev in rows)
    targets_with_anchor_derived_key = {
        tid
        for (tid, _key_type, _value), ev in rows
        if ev.from_start_anchor or ev.from_end_anchor
    }

    summary = {
        "stage": "04C",
        "release_ids": {
            "START": args.start_release_id,
            "END": args.end_release_id,
        },
        "policy": {
            "keys_frozen_before_lineage_scan": True,
            "recursive_key_expansion": False,
            "sequence_candidates_generate_lineage_keys": False,
            "target_accession_generates_accession_key": True,
            "eligible_direct_anchors_generate_lineage_keys": True,
            "candidate_generating_key_types": [
                "ACCESSION",
                "ORDERED_LOCUS",
                "ORF",
            ],
            "gene_name_candidate_generating": False,
            "gene_synonym_candidate_generating": False,
            "go_candidate_generating": False,
            "sv_alone_candidate_generating": False,
        },
        "input_anchor_rows": {
            "start_total": start_total,
            "start_eligible": start_eligible,
            "end_total": end_total,
            "end_eligible": end_eligible,
        },
        "target_count": len(targets),
        "lineage_key_count": len(rows),
        "lineage_key_type_counts": dict(sorted(by_type.items())),
        "targets_with_anchor_derived_key": len(
            targets_with_anchor_derived_key
        ),
    }

    with args.summary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)
        handle.write("\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
