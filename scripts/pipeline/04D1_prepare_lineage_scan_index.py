#!/usr/bin/env python3
"""
Production ProBE candidate pipeline.

Stage 04D1: compile the frozen lineage keys into a release-specific scan index.

Purpose
-------
This is a provenance/normalization stage only.  It does NOT scan UniProt and
does NOT decide SAME_ENTITY.

For each frozen target lineage key from 04C, the program:
  * attaches the target's release-specific primary taxonomy bucket;
  * computes how many targets share that exact typed key inside that bucket;
  * reconstructs the exact source role(s) of the key from the canonical target
    accession and the taxonomically eligible direct anchors from 04B.

The output therefore makes lineage-key collisions explicit before the full V2
scan and preserves directional provenance such as:
  TARGET_ACCESSION
  START:PRIMARY_ACCESSION
  START:SECONDARY_ACCESSION
  START:ORDERED_LOCUS
  START:ORF
  END:...

Stage-03 sequence candidates are not inputs and cannot contribute lineage keys.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

Key = tuple[str, str, str]  # target_id, key_type, key_value


def split_csv(value: str) -> list[str]:
    return [x for x in value.split(",") if x] if value else []


def parse_gene(gene: str) -> dict[str, set[str]]:
    result = {"N": set(), "S": set(), "L": set(), "O": set()}
    if not gene:
        return result
    for group in gene.split("|"):
        for field in group.split(";"):
            if len(field) < 3 or field[1] != ":" or field[0] not in result:
                continue
            for value in field[2:].split(","):
                if value:
                    result[field[0]].add(value)
    return result


def load_targets(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        required = {"target_id", "optional_accession"}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError(f"{path}: missing target_id/optional_accession")
        for row in reader:
            tid = row["target_id"]
            if tid in out:
                raise ValueError(f"{path}: duplicate target_id {tid}")
            out[tid] = row["optional_accession"]
    return out


def load_buckets(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        required = {"target_id", "primary_bucket"}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError(f"{path}: missing target_id/primary_bucket")
        for row in reader:
            tid = row["target_id"]
            if tid in out:
                raise ValueError(f"{path}: duplicate target_id {tid}")
            out[tid] = row["primary_bucket"]
    return out


def load_keys(path: Path) -> tuple[list[dict[str, str]], set[Key]]:
    rows: list[dict[str, str]] = []
    keys: set[Key] = set()
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        required = {
            "target_id",
            "key_type",
            "key_value",
            "from_target_accession",
            "from_start_anchor",
            "from_end_anchor",
        }
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            missing = required.difference(reader.fieldnames or ())
            raise ValueError(f"{path}: missing columns {sorted(missing)}")
        for row in reader:
            key = (row["target_id"], row["key_type"], row["key_value"])
            if key in keys:
                raise ValueError(f"{path}: duplicate frozen key {key}")
            keys.add(key)
            rows.append(row)
    return rows, keys


def add_origin(
    origins: dict[Key, set[str]],
    key: Key,
    role: str,
) -> None:
    origins[key].add(role)


def consume_anchor_origins(
    path: Path,
    expected_release: str,
    source_tag: str,
    origins: dict[Key, set[str]],
) -> int:
    eligible_rows = 0
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        required = {
            "release",
            "target_id",
            "primary_accession",
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
            if row["key_source_eligible"] != "1":
                continue
            eligible_rows += 1
            tid = row["target_id"]
            tag = source_tag

            add_origin(
                origins,
                (tid, "ACCESSION", row["primary_accession"]),
                f"{tag}:PRIMARY_ACCESSION",
            )
            for sec in split_csv(row["sec"]):
                add_origin(
                    origins,
                    (tid, "ACCESSION", sec),
                    f"{tag}:SECONDARY_ACCESSION",
                )

            genes = parse_gene(row["gene"])
            for value in genes["L"]:
                add_origin(
                    origins,
                    (tid, "ORDERED_LOCUS", value),
                    f"{tag}:ORDERED_LOCUS",
                )
            for value in genes["O"]:
                add_origin(
                    origins,
                    (tid, "ORF", value),
                    f"{tag}:ORF",
                )
    return eligible_rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keys", type=Path, required=True)
    ap.add_argument("--targets", type=Path, required=True)
    ap.add_argument("--start-anchors", type=Path, required=True)
    ap.add_argument("--end-anchors", type=Path, required=True)
    ap.add_argument("--start-target-taxonomy", type=Path, required=True)
    ap.add_argument("--end-target-taxonomy", type=Path, required=True)
    ap.add_argument("--start-release-id", required=True)
    ap.add_argument("--end-release-id", required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--summary", type=Path, required=True)
    args = ap.parse_args()

    for path in (
        args.keys,
        args.targets,
        args.start_anchors,
        args.end_anchors,
        args.start_target_taxonomy,
        args.end_target_taxonomy,
    ):
        if not path.is_file():
            raise FileNotFoundError(path)

    for path in (args.output, args.summary):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)

    targets = load_targets(args.targets)
    start_buckets = load_buckets(args.start_target_taxonomy)
    end_buckets = load_buckets(args.end_target_taxonomy)
    key_rows, frozen_keys = load_keys(args.keys)

    if set(targets) != set(start_buckets) or set(targets) != set(end_buckets):
        raise ValueError("target ID sets differ between canonical and taxonomy TSVs")

    origins: dict[Key, set[str]] = defaultdict(set)
    for tid, accession in targets.items():
        if accession:
            add_origin(origins, (tid, "ACCESSION", accession), "TARGET_ACCESSION")

    start_eligible = consume_anchor_origins(
        args.start_anchors,
        args.start_release_id,
        "START",
        origins,
    )
    end_eligible = consume_anchor_origins(
        args.end_anchors,
        args.end_release_id,
        "END",
        origins,
    )

    orphan_frozen_keys = sorted(frozen_keys.difference(origins))
    extra_origin_keys = sorted(set(origins).difference(frozen_keys))
    if orphan_frozen_keys:
        raise ValueError(
            "frozen keys without reconstructable provenance; first examples: "
            + repr(orphan_frozen_keys[:10])
        )
    if extra_origin_keys:
        raise ValueError(
            "anchor/target provenance generated keys absent from frozen 04C set; "
            "first examples: " + repr(extra_origin_keys[:10])
        )

    releases = [
        (args.start_release_id, start_buckets),
        (args.end_release_id, end_buckets),
    ]

    multiplicity: Counter[tuple[str, str, str, str]] = Counter()
    for row in key_rows:
        tid = row["target_id"]
        for release, buckets in releases:
            bucket = buckets[tid]
            if not bucket:
                raise ValueError(f"empty primary bucket for target {tid}")
            multiplicity[
                (release, bucket, row["key_type"], row["key_value"])
            ] += 1

    output_rows: list[dict[str, str | int]] = []
    collision_group_counts: Counter[tuple[str, str]] = Counter()
    max_multiplicity: Counter[tuple[str, str]] = Counter()

    seen_collision_groups: set[tuple[str, str, str, str]] = set()

    for row in key_rows:
        tid = row["target_id"]
        key = (tid, row["key_type"], row["key_value"])
        source_roles = ",".join(sorted(origins[key]))

        for release, buckets in releases:
            primary_bucket = buckets[tid]
            group = (
                release,
                primary_bucket,
                row["key_type"],
                row["key_value"],
            )
            m = multiplicity[group]
            if m > 1 and group not in seen_collision_groups:
                seen_collision_groups.add(group)
                collision_group_counts[(release, row["key_type"])] += 1
            max_multiplicity[(release, row["key_type"])] = max(
                max_multiplicity[(release, row["key_type"])], m
            )

            output_rows.append(
                {
                    "release": release,
                    "target_id": tid,
                    "primary_bucket": primary_bucket,
                    "key_type": row["key_type"],
                    "key_value": row["key_value"],
                    "key_target_multiplicity": m,
                    "key_collision": int(m > 1),
                    "from_target_accession": row["from_target_accession"],
                    "from_start_anchor": row["from_start_anchor"],
                    "from_end_anchor": row["from_end_anchor"],
                    "source_roles": source_roles,
                }
            )

    output_rows.sort(
        key=lambda r: (
            str(r["release"]),
            str(r["primary_bucket"]),
            str(r["key_type"]),
            str(r["key_value"]),
            str(r["target_id"]),
        )
    )

    fields = [
        "release",
        "target_id",
        "primary_bucket",
        "key_type",
        "key_value",
        "key_target_multiplicity",
        "key_collision",
        "from_target_accession",
        "from_start_anchor",
        "from_end_anchor",
        "source_roles",
    ]
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=fields, delimiter="\t", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(output_rows)

    bucket_changes = sum(
        start_buckets[tid] != end_buckets[tid] for tid in targets
    )

    per_release: dict[str, dict[str, object]] = {}
    for release, _buckets in releases:
        by_type = {}
        for key_type in ("ACCESSION", "ORDERED_LOCUS", "ORF"):
            by_type[key_type] = {
                "collision_group_count":
                    collision_group_counts[(release, key_type)],
                "max_targets_per_key_within_bucket":
                    max_multiplicity[(release, key_type)],
            }
        per_release[release] = by_type

    summary = {
        "stage": "04D1",
        "frozen_target_key_count": len(key_rows),
        "scan_index_row_count": len(output_rows),
        "eligible_anchor_rows_used_for_provenance": {
            args.start_release_id: start_eligible,
            args.end_release_id: end_eligible,
        },
        "targets_with_start_end_primary_bucket_difference": bucket_changes,
        "release_key_type_collision_stats": per_release,
        "policy": {
            "sequence_candidates_generate_lineage_keys": False,
            "keys_are_frozen": True,
            "key_collision_is_discovery_metadata_not_identity_decision": True,
            "source_roles_are_explicit": True,
        },
    }

    with args.summary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)
        handle.write("\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
