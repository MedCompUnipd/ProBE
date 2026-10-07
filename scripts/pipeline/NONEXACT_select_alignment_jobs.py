#!/usr/bin/env python3
"""
Production ProBE candidate pipeline.

Stage NONEXACT: select Stage-06A canonical alignment jobs that require
dynamic programming.

Input:
- Stage 06A canonical alignment job table.

Output:
- the exact Stage-06A rows for which exact_sequence == 0.

This stage performs no alignment, no scoring, no filtering based on alignment
properties, and no SAME_ENTITY decision.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

KEY = ("release", "target_id", "rid")


def positive_int(value: str, label: str) -> int:
    try:
        result = int(value)
    except ValueError as exc:
        raise ValueError(f"{label}: invalid integer {value!r}") from exc

    if result <= 0:
        raise ValueError(f"{label}: expected positive integer")

    return result


def row_key(row: dict[str, str]) -> tuple[str, str, int]:
    return (
        row["release"],
        row["target_id"],
        positive_int(
            row["rid"],
            "alignment-job RID",
        ),
    )


def main() -> int:
    ap = argparse.ArgumentParser()

    ap.add_argument(
        "--alignment-jobs",
        type=Path,
        required=True,
    )
    ap.add_argument(
        "--start-release-id",
        required=True,
    )
    ap.add_argument(
        "--end-release-id",
        required=True,
    )
    ap.add_argument(
        "--output",
        type=Path,
        required=True,
    )
    ap.add_argument(
        "--summary",
        type=Path,
        required=True,
    )

    args = ap.parse_args()

    if args.start_release_id == args.end_release_id:
        raise ValueError("START and END release IDs must be distinct")

    if not args.alignment_jobs.is_file():
        raise FileNotFoundError(args.alignment_jobs)

    for path in (
        args.output,
        args.summary,
    ):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite: {path}")
        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

    allowed_releases = {
        args.start_release_id,
        args.end_release_id,
    }

    input_count = 0
    exact_count = 0
    nonexact_count = 0

    input_release_counts = Counter()
    nonexact_release_counts = Counter()
    nonexact_scope_counts = Counter()
    nonexact_channel_counts = Counter()

    previous_key = None

    with (
        args.alignment_jobs.open(
            "r",
            encoding="utf-8",
            newline="",
        ) as input_handle,
        args.output.open(
            "w",
            encoding="utf-8",
            newline="",
        ) as output_handle,
    ):
        reader = csv.DictReader(
            input_handle,
            delimiter="\t",
        )

        required = {
            "release",
            "target_id",
            "rid",
            "job_scope",
            "discovery_channel",
            "exact_sequence",
        }

        if reader.fieldnames is None:
            raise ValueError(f"{args.alignment_jobs}: missing header")

        missing = required.difference(reader.fieldnames)

        if missing:
            raise ValueError(
                f"{args.alignment_jobs}: missing columns {sorted(missing)}"
            )

        writer = csv.DictWriter(
            output_handle,
            fieldnames=reader.fieldnames,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()

        for row in reader:
            input_count += 1

            release = row["release"]

            if release not in allowed_releases:
                raise ValueError(
                    f"{args.alignment_jobs}: unexpected release {release!r}"
                )

            key = row_key(row)

            if previous_key is not None and key <= previous_key:
                raise ValueError(
                    "06A alignment jobs are not "
                    "strictly sorted/unique: "
                    f"previous={previous_key} "
                    f"current={key}"
                )

            previous_key = key

            exact = row["exact_sequence"]

            if exact not in {"0", "1"}:
                raise ValueError(
                    f"{args.alignment_jobs}: invalid exact_sequence={exact!r} for {key}"
                )

            input_release_counts[release] += 1

            if exact == "1":
                exact_count += 1
                continue

            writer.writerow(row)

            nonexact_count += 1
            nonexact_release_counts[release] += 1
            nonexact_scope_counts[row["job_scope"]] += 1
            nonexact_channel_counts[row["discovery_channel"]] += 1

    if input_count != exact_count + nonexact_count:
        raise RuntimeError("NONEXACT input partition invariant violated")

    summary = {
        "stage": "NONEXACT",
        "releases": {
            "START": args.start_release_id,
            "END": args.end_release_id,
        },
        "input_alignment_job_count": input_count,
        "exact_job_count": exact_count,
        "nonexact_job_count": nonexact_count,
        "input_release_counts": {
            release: input_release_counts[release]
            for release in (
                args.start_release_id,
                args.end_release_id,
            )
        },
        "nonexact_release_counts": {
            release: nonexact_release_counts[release]
            for release in (
                args.start_release_id,
                args.end_release_id,
            )
        },
        "nonexact_scope_counts": dict(sorted(nonexact_scope_counts.items())),
        "nonexact_discovery_channel_counts": dict(
            sorted(nonexact_channel_counts.items())
        ),
        "policy": {
            "selection_only": True,
            "alignment_made_here": False,
            "scoring_made_here": False,
            "same_entity_decision_made_here": False,
            "exact_jobs_excluded": True,
            "nonexact_rows_preserved_from_06A": True,
        },
    }

    with args.summary.open(
        "w",
        encoding="utf-8",
        newline="\n",
    ) as handle:
        json.dump(
            summary,
            handle,
            indent=2,
            sort_keys=True,
        )
        handle.write("\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
