#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import os
from collections import Counter, defaultdict
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


def load_baseline(path: Path):
    rows = {}
    header = None

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")

        if reader.fieldnames is None:
            raise ValueError(f"{path}: missing header")

        header = list(reader.fieldnames)

        required = {
            "release",
            "target_id",
            "rid",
            "discovery_channel",
            "sequence_discovered",
            "lineage_discovered",
            "sequence_search_record_number",
            "full_v2_record_number",
        }

        missing = required.difference(header)
        if missing:
            raise ValueError(f"{path}: missing columns {sorted(missing)}")

        for row in reader:
            key = (
                row["release"],
                row["target_id"],
                row["rid"],
            )

            if key in rows:
                raise ValueError(f"{path}: duplicate baseline key {key}")

            row["go_positive_sequence_discovered"] = row["sequence_discovered"]
            row["full_v2_rescue_sequence_discovered"] = "0"

            rows[key] = row

    assert header is not None
    return header, rows


def load_rescue(
    path: Path,
    release: str,
    rows: dict,
):
    count = 0
    targets = set()

    required = {
        "record_number",
        "target_id",
        *SEQ_FACT_MAP.values(),
        *SEQ_EVIDENCE_FIELDS,
    }

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")

        if reader.fieldnames is None:
            raise ValueError(f"{path}: missing header")

        missing = required.difference(reader.fieldnames)
        if missing:
            raise ValueError(f"{path}: missing columns {sorted(missing)}")

        seen = set()

        for src in reader:
            count += 1

            key = (
                release,
                src["target_id"],
                src["rid"],
            )

            if key in seen:
                raise ValueError(f"{path}: duplicate rescue key {key}")
            seen.add(key)

            if key in rows:
                raise ValueError(
                    f"strict rescue overlaps baseline candidate universe: {key}"
                )

            if src["record_number"] != src["rid"]:
                raise ValueError(
                    f"{path}: FULL-V2 record_number/RID mismatch "
                    f"for {key}: record_number="
                    f"{src['record_number']} RID={src['rid']}"
                )

            row = {
                "release": release,
                "target_id": src["target_id"],
                "rid": src["rid"],
                "discovery_channel": "FULL_V2_RESCUE_SEQUENCE_ONLY",
                "sequence_discovered": "1",
                "go_positive_sequence_discovered": "0",
                "full_v2_rescue_sequence_discovered": "1",
                "lineage_discovered": "0",
                "sequence_search_record_number": "",
                "full_v2_record_number": src["record_number"],
            }

            for out_name, in_name in SEQ_FACT_MAP.items():
                row[out_name] = src[in_name]

            for name in SEQ_EVIDENCE_FIELDS:
                row[f"seq_{name}"] = src[name]

            rows[key] = row
            targets.add(src["target_id"])

    return count, targets


def main() -> int:
    ap = argparse.ArgumentParser()

    ap.add_argument(
        "--baseline-union",
        type=Path,
        required=True,
    )
    ap.add_argument(
        "--start-rescue",
        type=Path,
        required=True,
    )
    ap.add_argument(
        "--end-rescue",
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

    for path in (
        args.baseline_union,
        args.start_rescue,
        args.end_rescue,
    ):
        if not path.is_file():
            raise FileNotFoundError(path)

    for path in (args.output, args.summary):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)

    baseline_header, rows = load_baseline(args.baseline_union)

    baseline_count = len(rows)

    start_rescue_count, start_rescue_targets = load_rescue(
        args.start_rescue,
        args.start_release_id,
        rows,
    )

    end_rescue_count, end_rescue_targets = load_rescue(
        args.end_rescue,
        args.end_release_id,
        rows,
    )

    expected_total = baseline_count + start_rescue_count + end_rescue_count

    if len(rows) != expected_total:
        raise AssertionError(
            f"union arithmetic failed: expected {expected_total}, got {len(rows)}"
        )

    output_fields = list(baseline_header)

    insert_at = output_fields.index("sequence_discovered") + 1

    output_fields[insert_at:insert_at] = [
        "go_positive_sequence_discovered",
        "full_v2_rescue_sequence_discovered",
    ]

    ordered = sorted(
        rows.values(),
        key=lambda r: (
            r["release"],
            r["target_id"],
            int(r["rid"]),
        ),
    )

    output_tmp = Path(str(args.output) + ".tmp")
    summary_tmp = Path(str(args.summary) + ".tmp")

    channel_counts = Counter()
    release_channel_counts = defaultdict(Counter)
    provenance_counts = Counter()
    targets_by_release = defaultdict(set)

    with output_tmp.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=output_fields,
            delimiter="\t",
            lineterminator="\n",
            extrasaction="raise",
            restval="",
        )
        writer.writeheader()

        for row in ordered:
            writer.writerow(row)

            release = row["release"]

            channel_counts[row["discovery_channel"]] += 1
            release_channel_counts[release][row["discovery_channel"]] += 1

            provenance = (
                row.get(
                    "go_positive_sequence_discovered",
                    "0",
                ),
                row.get(
                    "full_v2_rescue_sequence_discovered",
                    "0",
                ),
                row.get("lineage_discovered", "0"),
            )

            provenance_counts[
                "GOSEQ={} RESCUESEQ={} LINEAGE={}".format(*provenance)
            ] += 1

            targets_by_release[release].add(row["target_id"])

    summary = {
        "stage": "05A2",
        "baseline_candidate_union_row_count": baseline_count,
        "start_full_v2_rescue_row_count": start_rescue_count,
        "end_full_v2_rescue_row_count": end_rescue_count,
        "candidate_union_row_count": len(ordered),
        "strict_rescue_target_count_by_release": {
            args.start_release_id: len(start_rescue_targets),
            args.end_release_id: len(end_rescue_targets),
        },
        "channel_counts": dict(sorted(channel_counts.items())),
        "release_channel_counts": {
            release: dict(sorted(counts.items()))
            for release, counts in sorted(release_channel_counts.items())
        },
        "provenance_combination_counts": dict(sorted(provenance_counts.items())),
        "targets_with_any_candidate_by_release": {
            release: len(targets)
            for release, targets in sorted(targets_by_release.items())
        },
        "metadata_conflicts_detected": 0,
        "baseline_overlap_with_strict_rescue": 0,
        "policy": {
            "union_key": [
                "release",
                "target_id",
                "rid",
            ],
            "same_entity_decision_made_here": False,
            "candidate_filtering_made_here": False,
            "alignment_made_here": False,
            "go_positive_sequence_provenance_preserved": True,
            "full_v2_rescue_sequence_provenance_preserved": True,
            "lineage_provenance_preserved": True,
            "sequence_search_record_number_semantics": (
                "GO-positive filtered FASTA ordinal only"
            ),
            "full_v2_record_number_semantics": "full V2 FASTA ordinal",
            "strict_rescue_uses_stage03_algorithm": True,
            "strict_rescue_changes_only_search_universe": True,
        },
    }

    with summary_tmp.open(
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

    os.replace(output_tmp, args.output)
    os.replace(summary_tmp, args.summary)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
