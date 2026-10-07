#!/usr/bin/env python3
"""
Production ProBE candidate pipeline.

Stage 05R0: prepare the strict full-V2 sequence-rescue target subset.

This stage makes no biological identity decision and does not modify any
upstream target fact. It selects, for one release, exactly the Stage-05B
uncovered targets for which k-mer sequence discovery was algorithmically
searchable, then emits lossless subsets of the canonical Stage-01 targets and
Stage-02B target taxonomy.

The configured k is deliberately not consumed here. Stage 05B is authoritative
for the derived kmer_sequence_searchable fact.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

RESCUE_TRIGGER = "STAGE05B_UNCOVERED_AND_K_SEARCHABLE"


def load_rescue_ids(path: Path, release_id: str) -> list[str]:
    selected: set[str] = set()

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        required = {
            "release",
            "target_id",
            "kmer_sequence_searchable",
        }
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            missing = required.difference(reader.fieldnames or ())
            raise ValueError(f"{path}: missing columns {sorted(missing)}")

        seen_pairs: set[tuple[str, str]] = set()

        for row in reader:
            release = row["release"]
            target_id = row["target_id"]
            searchable = row["kmer_sequence_searchable"]

            pair = (release, target_id)
            if pair in seen_pairs:
                raise ValueError(
                    f"{path}: duplicate release/target_id {release}/{target_id}"
                )
            seen_pairs.add(pair)

            if searchable not in {"0", "1"}:
                raise ValueError(
                    f"{path}: invalid kmer_sequence_searchable "
                    f"{searchable!r} for {release}/{target_id}"
                )

            if release == release_id and searchable == "1":
                selected.add(target_id)

    return sorted(selected)


def load_index(
    path: Path,
) -> tuple[list[str], dict[str, dict[str, str]]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames is None or "target_id" not in reader.fieldnames:
            raise ValueError(f"{path}: missing target_id column")

        fields = list(reader.fieldnames)
        rows: dict[str, dict[str, str]] = {}

        for row in reader:
            target_id = row["target_id"]
            if target_id in rows:
                raise ValueError(f"{path}: duplicate target_id {target_id}")
            rows[target_id] = row

    return fields, rows


def write_subset(
    path: Path,
    fields: list[str],
    source: dict[str, dict[str, str]],
    target_ids: list[str],
    source_label: str,
) -> None:
    missing = [target_id for target_id in target_ids if target_id not in source]
    if missing:
        raise ValueError(
            f"{source_label}: missing rescue targets {missing[:10]}"
        )

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fields,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        for target_id in target_ids:
            writer.writerow(source[target_id])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uncovered", type=Path, required=True)
    ap.add_argument("--targets", type=Path, required=True)
    ap.add_argument("--target-taxonomy", type=Path, required=True)
    ap.add_argument("--release-id", required=True)
    ap.add_argument("--output-ids", type=Path, required=True)
    ap.add_argument("--output-targets", type=Path, required=True)
    ap.add_argument("--output-target-taxonomy", type=Path, required=True)
    ap.add_argument("--summary", type=Path, required=True)
    args = ap.parse_args()

    for path in (
        args.uncovered,
        args.targets,
        args.target_taxonomy,
    ):
        if not path.is_file():
            raise FileNotFoundError(path)

    outputs = (
        args.output_ids,
        args.output_targets,
        args.output_target_taxonomy,
        args.summary,
    )
    for path in outputs:
        if path.exists():
            raise FileExistsError(f"refusing to overwrite: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)

    rescue_ids = load_rescue_ids(args.uncovered, args.release_id)

    target_fields, targets = load_index(args.targets)
    taxonomy_fields, target_taxonomy = load_index(args.target_taxonomy)

    missing_targets = [
        target_id for target_id in rescue_ids if target_id not in targets
    ]
    missing_taxonomy = [
        target_id
        for target_id in rescue_ids
        if target_id not in target_taxonomy
    ]

    if missing_targets:
        raise ValueError(
            f"Stage 01 missing rescue targets {missing_targets[:10]}"
        )
    if missing_taxonomy:
        raise ValueError(
            f"Stage 02B missing rescue target taxonomy "
            f"{missing_taxonomy[:10]}"
        )

    with args.output_ids.open("w", encoding="utf-8", newline="\n") as handle:
        for target_id in rescue_ids:
            handle.write(f"{target_id}\n")

    write_subset(
        args.output_targets,
        target_fields,
        targets,
        rescue_ids,
        "Stage 01",
    )
    write_subset(
        args.output_target_taxonomy,
        taxonomy_fields,
        target_taxonomy,
        rescue_ids,
        "Stage 02B",
    )

    summary = {
        "release": args.release_id,
        "rescue_target_count": len(rescue_ids),
        "rescue_trigger": RESCUE_TRIGGER,
        "source": args.uncovered.name,
    }

    with args.summary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)
        handle.write("\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
