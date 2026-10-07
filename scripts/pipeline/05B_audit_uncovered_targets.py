#!/usr/bin/env python3
"""
Production ProBE candidate pipeline.

Stage 05B: audit targets with no candidate after the lossless Stage-05A union.

This module makes NO biological identity decision and does not alter the
candidate universe.  It exists only to explain the small set of targets that
have zero candidates in START and/or END.

For each missing target/release it reports:
  * canonical target metadata;
  * whether k-mer sequence discovery was algorithmically possible (LEN >= configured k);
  * whether Stage 04A found a direct accession anchor;
  * whether Stage 04B deemed any direct anchor taxonomically eligible;
  * the Stage-04B ineligibility reasons, when present.

Pipeline invariant:
A target/release with a taxonomically eligible direct anchor MUST have at least
one lineage candidate in the Stage-05A union, because that anchor's accession
is itself a frozen lineage key and Stage 04D2 scans the full V2 release.
If this invariant is violated, the program writes the audit outputs and exits
non-zero.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AnchorAudit:
    direct_anchor_rows: int = 0
    eligible_anchor_rows: int = 0
    match_types: set[str] = field(default_factory=set)
    ineligible_reasons: set[str] = field(default_factory=set)
    primary_accessions: set[str] = field(default_factory=set)
    rids: set[str] = field(default_factory=set)
    frag_flags: set[str] = field(default_factory=set)


def load_targets(path: Path) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        required = {
            "target_id",
            "raw_taxid",
            "optional_accession",
            "sequence_length",
        }
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            missing = required.difference(reader.fieldnames or ())
            raise ValueError(f"{path}: missing columns {sorted(missing)}")

        for row in reader:
            tid = row["target_id"]
            if tid in result:
                raise ValueError(f"{path}: duplicate target_id {tid}")
            result[tid] = row
    return result


def load_union(
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
                raise ValueError(f"{path}: unexpected release {release}")
            covered[release].add(row["target_id"])

    return covered


def load_anchor_audit(
    path: Path,
    expected_release: str,
) -> dict[str, AnchorAudit]:
    result: dict[str, AnchorAudit] = defaultdict(AnchorAudit)

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        required = {
            "release",
            "target_id",
            "direct_match_type",
            "primary_accession",
            "rid",
            "frag",
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

            a = result[row["target_id"]]
            a.direct_anchor_rows += 1
            a.match_types.add(row["direct_match_type"])
            a.primary_accessions.add(row["primary_accession"])
            a.rids.add(row["rid"])
            a.frag_flags.add(row["frag"])

            if row["key_source_eligible"] == "1":
                a.eligible_anchor_rows += 1
            else:
                a.ineligible_reasons.add(row["key_source_reason"])

    return result


def encode(values: set[str]) -> str:
    return ",".join(sorted(values))


def classify_missing(length: int, audit: AnchorAudit, k: int) -> str:
    if audit.eligible_anchor_rows > 0:
        return "INVARIANT_VIOLATION_ELIGIBLE_DIRECT_ANCHOR_WITHOUT_CANDIDATE"

    if length < k:
        if audit.direct_anchor_rows == 0:
            return "TARGET_LEN_LT_K_AND_NO_DIRECT_ANCHOR"
        return "TARGET_LEN_LT_K_AND_DIRECT_ANCHOR_INELIGIBLE"

    if audit.direct_anchor_rows == 0:
        return "NO_SEQUENCE_CANDIDATE_AND_NO_DIRECT_ANCHOR"

    return "NO_SEQUENCE_CANDIDATE_AND_DIRECT_ANCHOR_INELIGIBLE"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", type=Path, required=True)
    ap.add_argument("--candidate-union", type=Path, required=True)
    ap.add_argument("--start-anchors", type=Path, required=True)
    ap.add_argument("--end-anchors", type=Path, required=True)
    ap.add_argument("--start-release-id", required=True)
    ap.add_argument("--end-release-id", required=True)
    ap.add_argument("--k", type=int, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--summary", type=Path, required=True)
    args = ap.parse_args()

    for path in (
        args.targets,
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

    targets = load_targets(args.targets)
    releases = (args.start_release_id, args.end_release_id)
    covered = load_union(args.candidate_union, releases)

    anchors = {
        args.start_release_id: load_anchor_audit(
            args.start_anchors, args.start_release_id
        ),
        args.end_release_id: load_anchor_audit(
            args.end_anchors, args.end_release_id
        ),
    }

    rows: list[dict[str, str | int]] = []
    reason_counts = {release: Counter() for release in releases}
    invariant_violations: list[tuple[str, str]] = []

    missing_by_release: dict[str, set[str]] = {}

    for release in releases:
        missing = set(targets).difference(covered[release])
        missing_by_release[release] = missing

        for tid in sorted(missing):
            target = targets[tid]
            length = int(target["sequence_length"])
            audit = anchors[release].get(tid, AnchorAudit())
            reason = classify_missing(length, audit, args.k)
            reason_counts[release][reason] += 1

            if reason.startswith("INVARIANT_VIOLATION"):
                invariant_violations.append((release, tid))

            rows.append(
                {
                    "release": release,
                    "target_id": tid,
                    "raw_taxid": target["raw_taxid"],
                    "optional_accession": target["optional_accession"],
                    "target_sequence_length": length,
                    "kmer_sequence_searchable": int(length >= args.k),
                    "direct_anchor_rows": audit.direct_anchor_rows,
                    "eligible_direct_anchor_rows":
                        audit.eligible_anchor_rows,
                    "direct_match_types": encode(audit.match_types),
                    "anchor_primary_accessions":
                        encode(audit.primary_accessions),
                    "anchor_rids": encode(audit.rids),
                    "anchor_frag_flags": encode(audit.frag_flags),
                    "ineligible_anchor_reasons":
                        encode(audit.ineligible_reasons),
                    "audit_reason": reason,
                }
            )

    fields = [
        "release",
        "target_id",
        "raw_taxid",
        "optional_accession",
        "target_sequence_length",
        "kmer_sequence_searchable",
        "direct_anchor_rows",
        "eligible_direct_anchor_rows",
        "direct_match_types",
        "anchor_primary_accessions",
        "anchor_rids",
        "anchor_frag_flags",
        "ineligible_anchor_reasons",
        "audit_reason",
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

    missing_both = (
        missing_by_release[args.start_release_id]
        & missing_by_release[args.end_release_id]
    )

    summary = {
        "stage": "05B",
        "target_count": len(targets),
        "covered_target_count_by_release": {
            release: len(covered[release]) for release in releases
        },
        "missing_target_count_by_release": {
            release: len(missing_by_release[release]) for release in releases
        },
        "missing_in_both_releases_count": len(missing_both),
        "missing_in_both_releases": sorted(missing_both),
        "missing_reason_counts_by_release": {
            release: dict(sorted(reason_counts[release].items()))
            for release in releases
        },
        "invariant_violation_count": len(invariant_violations),
        "invariant_violations": [
            {"release": release, "target_id": tid}
            for release, tid in invariant_violations
        ],
        "policy": {
            "candidate_universe_modified_here": False,
            "same_entity_decision_made_here": False,
            "kmer_algorithmic_searchability_length_threshold": args.k,
            "eligible_direct_anchor_without_union_candidate_is_error": True,
        },
    }

    with args.summary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)
        handle.write("\n")

    if invariant_violations:
        raise SystemExit(
            f"ERROR: {len(invariant_violations)} eligible direct anchors "
            "lack a Stage-05A candidate; inspect audit output"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
