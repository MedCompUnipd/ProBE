#!/usr/bin/env python3
"""
Production ProBE candidate pipeline.

Stage 05R_POST: report target/release pairs that remain without a primary
candidate after strict FULL-V2 rescue.

This stage is report-only. It does not modify the candidate universe, perform
sequence search, promote taxonomy-audit rows, or make SAME_ENTITY decisions.
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
    direct_rows: int = 0
    eligible_rows: int = 0
    ineligible_reasons: set[str] = field(default_factory=set)


def parse_nonnegative_int(value: str, label: str) -> int:
    try:
        result = int(value)
    except ValueError as exc:
        raise ValueError(f"{label}: expected integer, got {value!r}") from exc
    if result < 0:
        raise ValueError(f"{label}: expected nonnegative integer")
    return result


def load_uncovered(
    path: Path,
    releases: tuple[str, str],
) -> dict[tuple[str, str], dict[str, str]]:
    required = {
        "release",
        "target_id",
        "raw_taxid",
        "optional_accession",
        "target_sequence_length",
        "kmer_sequence_searchable",
        "direct_anchor_rows",
        "eligible_direct_anchor_rows",
        "ineligible_anchor_reasons",
        "audit_reason",
    }

    result: dict[tuple[str, str], dict[str, str]] = {}

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            missing = required.difference(reader.fieldnames or ())
            raise ValueError(f"{path}: missing columns {sorted(missing)}")

        for row in reader:
            release = row["release"]
            target_id = row["target_id"]

            if release not in releases:
                raise ValueError(f"{path}: unexpected release {release!r}")

            key = (release, target_id)
            if key in result:
                raise ValueError(
                    f"{path}: duplicate release/target_id {release}/{target_id}"
                )

            if row["kmer_sequence_searchable"] not in {"0", "1"}:
                raise ValueError(
                    f"{path}: invalid kmer_sequence_searchable for "
                    f"{release}/{target_id}"
                )

            parse_nonnegative_int(
                row["target_sequence_length"],
                f"{path}:{release}/{target_id}:target_sequence_length",
            )
            parse_nonnegative_int(
                row["direct_anchor_rows"],
                f"{path}:{release}/{target_id}:direct_anchor_rows",
            )
            parse_nonnegative_int(
                row["eligible_direct_anchor_rows"],
                f"{path}:{release}/{target_id}:eligible_direct_anchor_rows",
            )

            result[key] = row

    return result


def load_rescue_ids(path: Path) -> set[str]:
    result: set[str] = set()

    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            target_id = line.strip()
            if not target_id:
                continue
            if target_id in result:
                raise ValueError(
                    f"{path}:{line_number}: duplicate target_id {target_id}"
                )
            result.add(target_id)

    return result


def load_rescue_pairs(
    path: Path,
    release: str,
) -> tuple[Counter[str], set[tuple[str, str, str]]]:
    required = {"target_id", "rid", "record_number"}

    counts: Counter[str] = Counter()
    keys: set[tuple[str, str, str]] = set()

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            missing = required.difference(reader.fieldnames or ())
            raise ValueError(f"{path}: missing columns {sorted(missing)}")

        for row in reader:
            target_id = row["target_id"]
            rid = row["rid"]

            if row["record_number"] != rid:
                raise ValueError(f"{path}: record_number != rid for {target_id}/{rid}")

            key = (release, target_id, rid)
            if key in keys:
                raise ValueError(f"{path}: duplicate rescue key {key}")

            keys.add(key)
            counts[target_id] += 1

    return counts, keys


def load_final_union(
    path: Path,
    releases: tuple[str, str],
) -> tuple[Counter[tuple[str, str]], set[tuple[str, str, str]]]:
    required = {
        "release",
        "target_id",
        "rid",
        "full_v2_rescue_sequence_discovered",
    }

    counts: Counter[tuple[str, str]] = Counter()
    rescue_keys: set[tuple[str, str, str]] = set()
    all_keys: set[tuple[str, str, str]] = set()

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            missing = required.difference(reader.fieldnames or ())
            raise ValueError(f"{path}: missing columns {sorted(missing)}")

        for row in reader:
            release = row["release"]
            target_id = row["target_id"]
            rid = row["rid"]

            if release not in releases:
                raise ValueError(f"{path}: unexpected release {release!r}")

            key = (release, target_id, rid)
            if key in all_keys:
                raise ValueError(f"{path}: duplicate final union key {key}")
            all_keys.add(key)

            rescue = row["full_v2_rescue_sequence_discovered"]
            if rescue not in {"0", "1"}:
                raise ValueError(f"{path}: invalid rescue provenance for {key}")

            counts[(release, target_id)] += 1

            if rescue == "1":
                rescue_keys.add(key)

    return counts, rescue_keys


def load_taxonomy(
    path: Path,
) -> dict[str, dict[str, str]]:
    required = {
        "target_id",
        "resolution_status",
        "resolved_taxid",
        "resolved_rank",
        "species_status",
        "species_taxid",
        "primary_bucket",
    }

    result: dict[str, dict[str, str]] = {}

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            missing = required.difference(reader.fieldnames or ())
            raise ValueError(f"{path}: missing columns {sorted(missing)}")

        for row in reader:
            target_id = row["target_id"]
            if target_id in result:
                raise ValueError(f"{path}: duplicate target_id {target_id}")
            result[target_id] = row

    return result


def load_anchor_audit(
    path: Path,
    expected_release: str,
) -> dict[str, AnchorAudit]:
    required = {
        "release",
        "target_id",
        "key_source_eligible",
        "key_source_reason",
    }

    result: dict[str, AnchorAudit] = defaultdict(AnchorAudit)

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            missing = required.difference(reader.fieldnames or ())
            raise ValueError(f"{path}: missing columns {sorted(missing)}")

        for row in reader:
            if row["release"] != expected_release:
                raise ValueError(
                    f"{path}: expected {expected_release}, found {row['release']}"
                )

            eligible = row["key_source_eligible"]
            if eligible not in {"0", "1"}:
                raise ValueError(f"{path}: invalid key_source_eligible {eligible!r}")

            audit = result[row["target_id"]]
            audit.direct_rows += 1

            if eligible == "1":
                audit.eligible_rows += 1
            else:
                audit.ineligible_reasons.add(row["key_source_reason"])

    return result


def load_taxonomy_audit(
    path: Path,
    releases: tuple[str, str],
) -> tuple[Counter[tuple[str, str]], Counter[tuple[str, str]]]:
    required = {
        "release",
        "target_id",
        "rid",
        "audit_alignment_allowed",
    }

    rows: Counter[tuple[str, str]] = Counter()
    alignable: Counter[tuple[str, str]] = Counter()
    seen: set[tuple[str, str, str]] = set()

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            missing = required.difference(reader.fieldnames or ())
            raise ValueError(f"{path}: missing columns {sorted(missing)}")

        for row in reader:
            release = row["release"]
            target_id = row["target_id"]
            rid = row["rid"]

            if release not in releases:
                raise ValueError(f"{path}: unexpected release {release!r}")

            key = (release, target_id, rid)
            if key in seen:
                raise ValueError(f"{path}: duplicate audit key {key}")
            seen.add(key)

            allowed = row["audit_alignment_allowed"]
            if allowed not in {"0", "1"}:
                raise ValueError(f"{path}: invalid audit_alignment_allowed {allowed!r}")

            pair = (release, target_id)
            rows[pair] += 1
            if allowed == "1":
                alignable[pair] += 1

    return rows, alignable


def encode(values: set[str]) -> str:
    return ",".join(sorted(values))


def main() -> int:
    ap = argparse.ArgumentParser()

    ap.add_argument("--uncovered", type=Path, required=True)

    ap.add_argument("--start-rescue-ids", type=Path, required=True)
    ap.add_argument("--end-rescue-ids", type=Path, required=True)

    ap.add_argument("--start-rescue-pairs", type=Path, required=True)
    ap.add_argument("--end-rescue-pairs", type=Path, required=True)

    ap.add_argument("--final-union", type=Path, required=True)
    ap.add_argument("--taxonomy-audit", type=Path, required=True)

    ap.add_argument("--start-target-taxonomy", type=Path, required=True)
    ap.add_argument("--end-target-taxonomy", type=Path, required=True)

    ap.add_argument("--start-anchors", type=Path, required=True)
    ap.add_argument("--end-anchors", type=Path, required=True)

    ap.add_argument("--start-release-id", required=True)
    ap.add_argument("--end-release-id", required=True)

    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--summary", type=Path, required=True)

    args = ap.parse_args()

    if args.start_release_id == args.end_release_id:
        raise ValueError("START and END release IDs must be distinct")

    input_paths = (
        args.uncovered,
        args.start_rescue_ids,
        args.end_rescue_ids,
        args.start_rescue_pairs,
        args.end_rescue_pairs,
        args.final_union,
        args.taxonomy_audit,
        args.start_target_taxonomy,
        args.end_target_taxonomy,
        args.start_anchors,
        args.end_anchors,
    )

    for path in input_paths:
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

    uncovered = load_uncovered(args.uncovered, releases)

    rescue_ids = {
        args.start_release_id: load_rescue_ids(args.start_rescue_ids),
        args.end_release_id: load_rescue_ids(args.end_rescue_ids),
    }

    start_pair_counts, start_pair_keys = load_rescue_pairs(
        args.start_rescue_pairs,
        args.start_release_id,
    )
    end_pair_counts, end_pair_keys = load_rescue_pairs(
        args.end_rescue_pairs,
        args.end_release_id,
    )

    rescue_pair_counts = {
        args.start_release_id: start_pair_counts,
        args.end_release_id: end_pair_counts,
    }

    rescue_pair_keys = start_pair_keys | end_pair_keys

    final_counts, final_rescue_keys = load_final_union(
        args.final_union,
        releases,
    )

    if rescue_pair_keys != final_rescue_keys:
        missing = sorted(rescue_pair_keys - final_rescue_keys)[:10]
        unexpected = sorted(final_rescue_keys - rescue_pair_keys)[:10]
        raise RuntimeError(
            "05R_POST rescue/final-union invariant violation: "
            f"missing_in_05A2={missing} "
            f"unexpected_in_05A2={unexpected}"
        )

    taxonomy = {
        args.start_release_id: load_taxonomy(args.start_target_taxonomy),
        args.end_release_id: load_taxonomy(args.end_target_taxonomy),
    }

    anchors = {
        args.start_release_id: load_anchor_audit(
            args.start_anchors,
            args.start_release_id,
        ),
        args.end_release_id: load_anchor_audit(
            args.end_anchors,
            args.end_release_id,
        ),
    }

    audit_rows, audit_alignable = load_taxonomy_audit(
        args.taxonomy_audit,
        releases,
    )

    uncovered_by_release = {
        release: {
            target_id
            for candidate_release, target_id in uncovered
            if candidate_release == release
        }
        for release in releases
    }

    for release in releases:
        expected_rescue_ids = {
            target_id
            for candidate_release, target_id in uncovered
            if candidate_release == release
            and uncovered[(candidate_release, target_id)]["kmer_sequence_searchable"]
            == "1"
        }

        if rescue_ids[release] != expected_rescue_ids:
            raise RuntimeError(f"05R_POST {release} Stage 05R0 selection mismatch")

        pair_targets = set(rescue_pair_counts[release])
        if not pair_targets.issubset(rescue_ids[release]):
            raise RuntimeError(
                f"05R_POST {release} Stage 05R1 emitted pairs for non-rescue targets"
            )

    rows: list[dict[str, str | int]] = []
    recovered_by_release = {release: 0 for release in releases}
    unresolved_by_release = {release: set() for release in releases}

    for release in releases:
        for target_id in sorted(uncovered_by_release[release]):
            key = (release, target_id)
            baseline = uncovered[key]

            tax = taxonomy[release].get(target_id)
            if tax is None:
                raise RuntimeError(
                    f"05R_POST {release}/{target_id} missing Stage 02B taxonomy"
                )

            anchor = anchors[release].get(target_id, AnchorAudit())

            expected_direct_rows = parse_nonnegative_int(
                baseline["direct_anchor_rows"],
                f"{release}/{target_id}:direct_anchor_rows",
            )
            expected_eligible_rows = parse_nonnegative_int(
                baseline["eligible_direct_anchor_rows"],
                f"{release}/{target_id}:eligible_direct_anchor_rows",
            )

            if anchor.direct_rows != expected_direct_rows:
                raise RuntimeError(
                    f"05R_POST {release}/{target_id} direct-anchor count mismatch"
                )

            if anchor.eligible_rows != expected_eligible_rows:
                raise RuntimeError(
                    f"05R_POST {release}/{target_id} eligible-anchor count mismatch"
                )

            if (
                encode(anchor.ineligible_reasons)
                != baseline["ineligible_anchor_reasons"]
            ):
                raise RuntimeError(
                    f"05R_POST {release}/{target_id} ineligible-anchor reason mismatch"
                )

            selected = int(target_id in rescue_ids[release])
            pair_count = rescue_pair_counts[release][target_id]
            final_count = final_counts[key]

            if final_count > 0:
                if selected != 1 or pair_count == 0:
                    raise RuntimeError(
                        f"05R_POST {release}/{target_id} final primary "
                        "candidate lacks strict-rescue provenance"
                    )
                if final_count != pair_count:
                    raise RuntimeError(
                        f"05R_POST {release}/{target_id} final candidate "
                        "count differs from rescue pair count"
                    )

                recovered_by_release[release] += 1
                continue

            if pair_count != 0:
                raise RuntimeError(
                    f"05R_POST {release}/{target_id} rescue pair exists "
                    "without final primary candidate"
                )

            unresolved_by_release[release].add(target_id)

            rows.append(
                {
                    "release": release,
                    "target_id": target_id,
                    "raw_taxid": baseline["raw_taxid"],
                    "optional_accession": baseline["optional_accession"],
                    "target_sequence_length": baseline["target_sequence_length"],
                    "baseline_audit_reason": baseline["audit_reason"],
                    "kmer_sequence_searchable": baseline["kmer_sequence_searchable"],
                    "strict_rescue_selected": selected,
                    "strict_rescue_pair_count": pair_count,
                    "final_primary_candidate_count": final_count,
                    "taxonomy_audit_row_count": audit_rows[key],
                    "taxonomy_audit_alignment_allowed_row_count": audit_alignable[key],
                    "target_resolution_status": tax["resolution_status"],
                    "target_resolved_taxid": tax["resolved_taxid"],
                    "target_resolved_rank": tax["resolved_rank"],
                    "target_species_status": tax["species_status"],
                    "target_species_taxid": tax["species_taxid"],
                    "target_primary_bucket": tax["primary_bucket"],
                    "direct_anchor_rows": expected_direct_rows,
                    "eligible_direct_anchor_rows": expected_eligible_rows,
                    "ineligible_anchor_reasons": baseline["ineligible_anchor_reasons"],
                    "post_rescue_status": "UNRESOLVED_AFTER_STRICT_RESCUE",
                }
            )

    fields = [
        "release",
        "target_id",
        "raw_taxid",
        "optional_accession",
        "target_sequence_length",
        "baseline_audit_reason",
        "kmer_sequence_searchable",
        "strict_rescue_selected",
        "strict_rescue_pair_count",
        "final_primary_candidate_count",
        "taxonomy_audit_row_count",
        "taxonomy_audit_alignment_allowed_row_count",
        "target_resolution_status",
        "target_resolved_taxid",
        "target_resolved_rank",
        "target_species_status",
        "target_species_taxid",
        "target_primary_bucket",
        "direct_anchor_rows",
        "eligible_direct_anchor_rows",
        "ineligible_anchor_reasons",
        "post_rescue_status",
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

    unresolved_both = (
        unresolved_by_release[args.start_release_id]
        & unresolved_by_release[args.end_release_id]
    )

    summary = {
        "stage": "05R_POST",
        "releases": {
            "START": args.start_release_id,
            "END": args.end_release_id,
        },
        "baseline_uncovered_target_release_count": len(uncovered),
        "baseline_uncovered_count_by_release": {
            release: len(uncovered_by_release[release]) for release in releases
        },
        "strict_rescue_selected_count_by_release": {
            release: len(rescue_ids[release]) for release in releases
        },
        "strict_rescue_recovered_count_by_release": recovered_by_release,
        "post_rescue_unresolved_target_release_count": len(rows),
        "post_rescue_unresolved_count_by_release": {
            release: len(unresolved_by_release[release]) for release in releases
        },
        "post_rescue_unresolved_in_both_releases_count": len(unresolved_both),
        "post_rescue_unresolved_in_both_releases": sorted(unresolved_both),
        "unresolved_with_taxonomy_audit_count_by_release": {
            release: sum(
                audit_rows[(release, target_id)] > 0
                for target_id in unresolved_by_release[release]
            )
            for release in releases
        },
        "unresolved_with_alignment_allowed_audit_count_by_release": {
            release: sum(
                audit_alignable[(release, target_id)] > 0
                for target_id in unresolved_by_release[release]
            )
            for release in releases
        },
        "policy": {
            "report_only": True,
            "candidate_universe_modified_here": False,
            "taxonomy_audit_modifies_primary_status": False,
            "same_entity_decision_made_here": False,
            "strict_rescue_membership_taken_from_stage05r0": True,
            "final_primary_status_taken_from_stage05a2": True,
        },
    }

    with args.summary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)
        handle.write("\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
