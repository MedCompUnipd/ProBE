#!/usr/bin/env python3
"""
TESTING-only ProBE candidate-pipeline-v1

Stage 05C2: freeze taxonomy-incompatible direct accession anchors as a separate
AUDIT-ONLY channel.

This stage does NOT modify the final primary candidate universe produced by 05A2.

Policy
------
- SAME_SPECIES_WRONG_SUBTAXON:
    preserve as TAXONOMY_RESCUE_AUDIT and allow later whole-protein alignment
    for diagnostic/temporal continuity analysis only.
    It is NOT a primary candidate and cannot transfer GO by itself.

- CROSS_SPECIES:
    preserve as CROSS_SPECIES_AUDIT only; do NOT sequence-test it.

- all other ineligible reasons:
    preserve as audit only; do NOT sequence-test them.

The purpose is to keep the strict primary taxonomy policy intact while still
allowing a tiny, explicitly separated set of direct-accession taxonomy drifts
to be examined later for possible database/taxonomy evolution.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path


def load_primary_union(path: Path) -> set[tuple[str, str, str]]:
    rows: set[tuple[str, str, str]] = set()
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        required = {"release", "target_id", "rid"}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            missing = required.difference(reader.fieldnames or ())
            raise ValueError(f"{path}: missing columns {sorted(missing)}")
        for row in reader:
            key = (row["release"], row["target_id"], row["rid"])
            if key in rows:
                raise ValueError(f"{path}: duplicate union key {key}")
            rows.add(key)
    return rows


def load_covered_targets(
    path: Path,
    releases: tuple[str, str],
) -> dict[str, set[str]]:
    covered = {release: set() for release in releases}

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")

        required = {"release", "target_id"}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            missing = required.difference(reader.fieldnames or ())
            raise ValueError(f"{path}: missing columns {sorted(missing)}")

        for row in reader:
            release = row["release"]
            if release not in covered:
                raise ValueError(f"{path}: unexpected release {release!r}")
            covered[release].add(row["target_id"])

    return covered


def classify(reason: str) -> tuple[str, int]:
    if reason == "SAME_SPECIES_WRONG_SUBTAXON":
        return "TAXONOMY_RESCUE_AUDIT", 1
    if reason == "CROSS_SPECIES":
        return "CROSS_SPECIES_AUDIT", 0
    if reason == "UNRESOLVED_TAXONOMY":
        return "UNRESOLVED_TAXONOMY_AUDIT", 0
    if reason == "MULTIPLE_OR_MISSING_OX":
        return "MULTIPLE_OR_MISSING_OX_AUDIT", 0
    if reason == "TARGET_PRIMARY_BUCKET_UNRESOLVED":
        return "TARGET_BUCKET_UNRESOLVED_AUDIT", 0
    return "OTHER_INELIGIBLE_AUDIT", 0


def consume(
    path: Path,
    expected_release: str,
    covered_targets: dict[str, set[str]],
    primary_union: set[tuple[str, str, str]],
    rows: list[dict[str, str | int]],
    reason_counts: Counter[str],
    class_counts: Counter[str],
) -> None:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        required = {
            "release",
            "target_id",
            "target_optional_accession",
            "direct_match_type",
            "direct_match_accession",
            "primary_accession",
            "entry",
            "rid",
            "raw_ox",
            "frag",
            "sequence_length",
            "pe",
            "sv",
            "sec",
            "gene",
            "target_raw_taxid",
            "target_resolved_taxid",
            "target_resolved_rank",
            "target_species_taxid",
            "target_primary_bucket",
            "anchor_resolved_taxid",
            "anchor_resolved_rank",
            "anchor_species_taxid",
            "taxonomy_relation",
            "key_source_eligible",
            "key_source_reason",
        }
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            missing = required.difference(reader.fieldnames or ())
            raise ValueError(f"{path}: missing columns {sorted(missing)}")

        for row in reader:
            if row["release"] != expected_release:
                raise ValueError(
                    f"{path}: expected {expected_release}, found {row['release']}"
                )
            if row["key_source_eligible"] == "1":
                continue

            audit_class, alignment_allowed = classify(row["key_source_reason"])
            reason_counts[row["key_source_reason"]] += 1
            class_counts[audit_class] += 1

            union_key = (
                row["release"],
                row["target_id"],
                row["rid"],
            )

            rows.append(
                {
                    "release": row["release"],
                    "target_id": row["target_id"],
                    "target_optional_accession": row["target_optional_accession"],
                    "target_raw_taxid": row["target_raw_taxid"],
                    "target_resolved_taxid": row["target_resolved_taxid"],
                    "target_resolved_rank": row["target_resolved_rank"],
                    "target_species_taxid": row["target_species_taxid"],
                    "target_primary_bucket": row["target_primary_bucket"],
                    "target_has_primary_candidate": int(
                        row["target_id"] in covered_targets[row["release"]]
                    ),
                    "record_already_in_primary_union_for_target": int(
                        union_key in primary_union
                    ),
                    "audit_class": audit_class,
                    "audit_alignment_allowed": alignment_allowed,
                    "key_source_reason": row["key_source_reason"],
                    "taxonomy_relation": row["taxonomy_relation"],
                    "direct_match_type": row["direct_match_type"],
                    "direct_match_accession": row["direct_match_accession"],
                    "candidate_primary_accession": row["primary_accession"],
                    "entry": row["entry"],
                    "rid": row["rid"],
                    "raw_ox": row["raw_ox"],
                    "anchor_resolved_taxid": row["anchor_resolved_taxid"],
                    "anchor_resolved_rank": row["anchor_resolved_rank"],
                    "anchor_species_taxid": row["anchor_species_taxid"],
                    "frag": row["frag"],
                    "sequence_length": row["sequence_length"],
                    "pe": row["pe"],
                    "sv": row["sv"],
                    "sec": row["sec"],
                    "gene": row["gene"],
                }
            )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidate-union", type=Path, required=True)
    ap.add_argument("--start-anchors", type=Path, required=True)
    ap.add_argument("--end-anchors", type=Path, required=True)
    ap.add_argument("--start-release-id", required=True)
    ap.add_argument("--end-release-id", required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--summary", type=Path, required=True)
    args = ap.parse_args()

    if args.start_release_id == args.end_release_id:
        raise ValueError("START and END release IDs must be distinct")

    for path in (
        args.candidate_union,
        args.start_anchors,
        args.end_anchors,
    ):
        if not path.is_file():
            raise FileNotFoundError(path)

    for path in (args.output, args.summary):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)

    releases = (
        args.start_release_id,
        args.end_release_id,
    )

    primary_union = load_primary_union(args.candidate_union)
    covered_targets = load_covered_targets(
        args.candidate_union,
        releases,
    )

    rows: list[dict[str, str | int]] = []
    reason_counts: Counter[str] = Counter()
    class_counts: Counter[str] = Counter()

    consume(
        args.start_anchors,
        args.start_release_id,
        covered_targets,
        primary_union,
        rows,
        reason_counts,
        class_counts,
    )
    consume(
        args.end_anchors,
        args.end_release_id,
        covered_targets,
        primary_union,
        rows,
        reason_counts,
        class_counts,
    )

    rows.sort(
        key=lambda r: (
            str(r["release"]),
            str(r["target_id"]),
            int(str(r["rid"])),
        )
    )

    fields = [
        "release",
        "target_id",
        "target_optional_accession",
        "target_raw_taxid",
        "target_resolved_taxid",
        "target_resolved_rank",
        "target_species_taxid",
        "target_primary_bucket",
        "target_has_primary_candidate",
        "record_already_in_primary_union_for_target",
        "audit_class",
        "audit_alignment_allowed",
        "key_source_reason",
        "taxonomy_relation",
        "direct_match_type",
        "direct_match_accession",
        "candidate_primary_accession",
        "entry",
        "rid",
        "raw_ox",
        "anchor_resolved_taxid",
        "anchor_resolved_rank",
        "anchor_species_taxid",
        "frag",
        "sequence_length",
        "pe",
        "sv",
        "sec",
        "gene",
    ]

    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fields,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)

    alignable = [r for r in rows if r["audit_alignment_allowed"] == 1]
    cross_species = [r for r in rows if r["audit_class"] == "CROSS_SPECIES_AUDIT"]

    summary = {
        "stage": "05C2",
        "ineligible_direct_anchor_row_count": len(rows),
        "audit_alignment_allowed_row_count": len(alignable),
        "cross_species_audit_row_count": len(cross_species),
        "reason_counts": dict(sorted(reason_counts.items())),
        "audit_class_counts": dict(sorted(class_counts.items())),
        "alignment_allowed_target_count": len(
            {(r["release"], r["target_id"]) for r in alignable}
        ),
        "alignment_allowed_rows_for_targets_without_primary_candidate": sum(
            int(r["target_has_primary_candidate"]) == 0 for r in alignable
        ),
        "rows_already_in_primary_union_for_same_target": sum(
            int(r["record_already_in_primary_union_for_target"]) == 1 for r in rows
        ),
        "policy": {
            "primary_candidate_universe_modified_here": False,
            "same_species_wrong_subtaxon_alignment_audit_allowed": True,
            "same_species_wrong_subtaxon_becomes_primary_candidate": False,
            "cross_species_sequence_test_allowed": False,
            "same_entity_decision_made_here": False,
        },
    }

    with args.summary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)
        handle.write("\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
