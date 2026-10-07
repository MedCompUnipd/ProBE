#!/usr/bin/env python3
"""
Production ProBE candidate pipeline

Stage 06C1: finalize the canonical directional-glocal alignment table.

Inputs
------
1. Stage-06A alignment jobs (all canonical pairs, exact + nonexact).
2. Stage-06C full-DP output for NONEXACT pairs only.

Behavior
--------
- Strictly validates the NONEXACT table against Stage 06A.
- Streams both files in Stage-06A order without loading the full table.
- Synthesizes mathematically exact directional-glocal facts for exact pairs:
    score = 2 * sequence_length
    full coverage in both directions
    identity = 1
    zero gaps/mismatches
    zero DP cells
- Preserves the exact 06C directional schema.
- Makes NO SAME_ENTITY decision.

Refuses to overwrite outputs.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

KEY = ("release", "target_id", "rid")

BASE_FIELDS = [
    "release",
    "target_id",
    "rid",
    "job_scope",
    "discovery_channel",
    "target_optional_accession",
    "candidate_primary_accession",
    "target_length",
    "candidate_length",
]

DIR_SUFFIXES = [
    "status",
    "score",
    "target_start0",
    "target_end0_exclusive",
    "candidate_start0",
    "candidate_end0_exclusive",
    "target_aligned_span",
    "candidate_aligned_span",
    "target_coverage",
    "candidate_coverage",
    "matches",
    "mismatches",
    "gap_in_target_residues",
    "gap_in_candidate_residues",
    "gap_openings",
    "aligned_residue_pairs",
    "alignment_columns",
    "identity_aligned_pairs",
    "identity_alignment_columns",
    "cells_evaluated",
]

EXPECTED_FIELDS = (
    BASE_FIELDS
    + [f"target_global_{x}" for x in DIR_SUFFIXES]
    + [f"candidate_global_{x}" for x in DIR_SUFFIXES]
)


def row_key(row: dict[str, str]) -> tuple[str, str, str]:
    return tuple(row[x] for x in KEY)


def require_columns(fieldnames, required, path: Path) -> None:
    if fieldnames is None:
        raise ValueError(f"{path}: missing header")
    missing = set(required).difference(fieldnames)
    if missing:
        raise ValueError(f"{path}: missing columns {sorted(missing)}")


def exact_direction(prefix: str, n: int) -> dict[str, str]:
    return {
        f"{prefix}_status": "OK_EXACT",
        f"{prefix}_score": str(2 * n),
        f"{prefix}_target_start0": "0",
        f"{prefix}_target_end0_exclusive": str(n),
        f"{prefix}_candidate_start0": "0",
        f"{prefix}_candidate_end0_exclusive": str(n),
        f"{prefix}_target_aligned_span": str(n),
        f"{prefix}_candidate_aligned_span": str(n),
        f"{prefix}_target_coverage": "1.0000000000",
        f"{prefix}_candidate_coverage": "1.0000000000",
        f"{prefix}_matches": str(n),
        f"{prefix}_mismatches": "0",
        f"{prefix}_gap_in_target_residues": "0",
        f"{prefix}_gap_in_candidate_residues": "0",
        f"{prefix}_gap_openings": "0",
        f"{prefix}_aligned_residue_pairs": str(n),
        f"{prefix}_alignment_columns": str(n),
        f"{prefix}_identity_aligned_pairs": "1.0000000000",
        f"{prefix}_identity_alignment_columns": "1.0000000000",
        f"{prefix}_cells_evaluated": "0",
    }


def validate_directional_nonexact(row: dict[str, str], key) -> int:
    n = int(row["target_length"])
    m = int(row["candidate_length"])

    if row["target_global_status"] != "OK":
        raise ValueError(f"{key}: TARGET_GLOBAL status != OK")
    if row["candidate_global_status"] != "OK":
        raise ValueError(f"{key}: CANDIDATE_GLOBAL status != OK")

    # TARGET_GLOBAL must consume the complete target.
    if int(row["target_global_target_start0"]) != 0:
        raise ValueError(f"{key}: TARGET_GLOBAL target_start0 != 0")
    if int(row["target_global_target_end0_exclusive"]) != n:
        raise ValueError(f"{key}: TARGET_GLOBAL target end != target length")
    if int(row["target_global_target_aligned_span"]) != n:
        raise ValueError(f"{key}: TARGET_GLOBAL target span != target length")
    if row["target_global_target_coverage"] != "1.0000000000":
        raise ValueError(f"{key}: TARGET_GLOBAL target coverage != 1")

    # CANDIDATE_GLOBAL must consume the complete candidate.
    if int(row["candidate_global_candidate_start0"]) != 0:
        raise ValueError(f"{key}: CANDIDATE_GLOBAL candidate_start0 != 0")
    if int(row["candidate_global_candidate_end0_exclusive"]) != m:
        raise ValueError(f"{key}: CANDIDATE_GLOBAL candidate end != candidate length")
    if int(row["candidate_global_candidate_aligned_span"]) != m:
        raise ValueError(f"{key}: CANDIDATE_GLOBAL candidate span != candidate length")
    if row["candidate_global_candidate_coverage"] != "1.0000000000":
        raise ValueError(f"{key}: CANDIDATE_GLOBAL candidate coverage != 1")

    expected_cells = (n + 1) * (m + 1)
    tg_cells = int(row["target_global_cells_evaluated"])
    cg_cells = int(row["candidate_global_cells_evaluated"])
    if tg_cells != expected_cells or cg_cells != expected_cells:
        raise ValueError(
            f"{key}: DP cell mismatch expected={expected_cells} "
            f"target_global={tg_cells} candidate_global={cg_cells}"
        )
    return tg_cells + cg_cells


def validate_base_against_job(row, job, key) -> None:
    pairs = (
        ("release", "release"),
        ("target_id", "target_id"),
        ("rid", "rid"),
        ("job_scope", "job_scope"),
        ("discovery_channel", "discovery_channel"),
        ("target_optional_accession", "target_optional_accession"),
        ("candidate_primary_accession", "candidate_primary_accession"),
        ("target_length", "target_length"),
        ("candidate_length", "candidate_length"),
    )
    for out_col, job_col in pairs:
        if row[out_col] != job[job_col]:
            raise ValueError(
                f"{key}: metadata mismatch {out_col}: "
                f"06C={row[out_col]!r} 06A={job[job_col]!r}"
            )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--alignment-jobs", type=Path, required=True)
    ap.add_argument("--nonexact-alignments", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--summary", type=Path, required=True)
    ap.add_argument("--start-release-id", required=True)
    ap.add_argument("--end-release-id", required=True)
    args = ap.parse_args()

    if args.start_release_id == args.end_release_id:
        raise ValueError("START and END release IDs must be distinct")

    allowed_releases = {
        args.start_release_id,
        args.end_release_id,
    }

    for p in (args.alignment_jobs, args.nonexact_alignments):
        if not p.is_file():
            raise FileNotFoundError(p)

    for p in (args.output, args.summary):
        if p.exists():
            raise FileExistsError(f"refusing to overwrite: {p}")
        p.parent.mkdir(parents=True, exist_ok=True)

    total = 0
    exact_count = 0
    nonexact_count = 0
    total_dp_cells = 0

    release_counts = Counter()
    scope_counts = Counter()
    channel_counts = Counter()
    exact_by_release = Counter()
    nonexact_by_release = Counter()
    target_status_counts = Counter()
    candidate_status_counts = Counter()
    best_direction_counts = Counter()
    best_direction_by_channel = Counter()
    negative_counts = Counter()

    previous_key = None

    out_tmp = Path(str(args.output) + ".tmp")
    sum_tmp = Path(str(args.summary) + ".tmp")

    try:
        with (
            args.alignment_jobs.open("r", encoding="utf-8", newline="") as jh,
            args.nonexact_alignments.open("r", encoding="utf-8", newline="") as nh,
            out_tmp.open("w", encoding="utf-8", newline="") as oh,
        ):
            jr = csv.DictReader(jh, delimiter="\t")
            nr = csv.DictReader(nh, delimiter="\t")

            require_columns(
                jr.fieldnames,
                [
                    *BASE_FIELDS,
                    "exact_sequence",
                ],
                args.alignment_jobs,
            )

            if nr.fieldnames != EXPECTED_FIELDS:
                raise ValueError(
                    f"{args.nonexact_alignments}: unexpected schema; "
                    f"expected {len(EXPECTED_FIELDS)} columns, "
                    f"got {len(nr.fieldnames or [])}"
                )

            writer = csv.DictWriter(
                oh,
                fieldnames=EXPECTED_FIELDS,
                delimiter="\t",
                lineterminator="\n",
            )
            writer.writeheader()

            next_nonexact = next(nr, None)

            for job in jr:
                key = row_key(job)

                if job["release"] not in allowed_releases:
                    raise ValueError(f"{key}: unexpected release {job['release']!r}")

                if job["exact_sequence"] not in {"0", "1"}:
                    raise ValueError(
                        f"{key}: invalid exact_sequence {job['exact_sequence']!r}"
                    )

                sort_key = (job["release"], job["target_id"], int(job["rid"]))

                if previous_key is not None and sort_key <= previous_key:
                    raise ValueError(
                        f"06A jobs are not strictly sorted/unique: "
                        f"previous={previous_key} current={sort_key}"
                    )
                previous_key = sort_key

                total += 1
                release_counts[job["release"]] += 1
                scope_counts[job["job_scope"]] += 1
                channel_counts[job["discovery_channel"]] += 1

                exact = job["exact_sequence"] == "1"

                if exact:
                    exact_count += 1
                    exact_by_release[job["release"]] += 1

                    n = int(job["target_length"])
                    m = int(job["candidate_length"])
                    if n != m:
                        raise ValueError(
                            f"{key}: exact pair has unequal lengths {n} != {m}"
                        )

                    row = {
                        "release": job["release"],
                        "target_id": job["target_id"],
                        "rid": job["rid"],
                        "job_scope": job["job_scope"],
                        "discovery_channel": job["discovery_channel"],
                        "target_optional_accession": job["target_optional_accession"],
                        "candidate_primary_accession": job[
                            "candidate_primary_accession"
                        ],
                        "target_length": job["target_length"],
                        "candidate_length": job["candidate_length"],
                    }
                    row.update(exact_direction("target_global", n))
                    row.update(exact_direction("candidate_global", n))

                else:
                    nonexact_count += 1
                    nonexact_by_release[job["release"]] += 1

                    if next_nonexact is None:
                        raise ValueError(f"NONEXACT table ended before job {key}")

                    nk = row_key(next_nonexact)
                    if nk != key:
                        raise ValueError(
                            f"NONEXACT stream/key mismatch: expected={key} got={nk}"
                        )

                    row = next_nonexact
                    validate_base_against_job(row, job, key)
                    total_dp_cells += validate_directional_nonexact(row, key)
                    next_nonexact = next(nr, None)

                target_status_counts[row["target_global_status"]] += 1
                candidate_status_counts[row["candidate_global_status"]] += 1

                ts = int(row["target_global_score"])
                cs = int(row["candidate_global_score"])
                if ts > cs:
                    best = "TARGET_GLOBAL"
                elif cs > ts:
                    best = "CANDIDATE_GLOBAL"
                else:
                    best = "TIE"
                best_direction_counts[best] += 1
                best_direction_by_channel[(job["discovery_channel"], best)] += 1

                if ts < 0:
                    negative_counts["TARGET_GLOBAL"] += 1
                if cs < 0:
                    negative_counts["CANDIDATE_GLOBAL"] += 1
                if ts < 0 and cs < 0:
                    negative_counts["BOTH_DIRECTIONS_NEGATIVE"] += 1

                writer.writerow(row)

            if next_nonexact is not None:
                raise ValueError(
                    f"NONEXACT table contains extra row {row_key(next_nonexact)}"
                )

        summary = {
            "stage": "06C1",
            "releases": {
                "START": args.start_release_id,
                "END": args.end_release_id,
            },
            "canonical_pair_count": total,
            "exact_pair_count": exact_count,
            "nonexact_pair_count": nonexact_count,
            "directional_alignment_count": total * 2,
            "nonexact_directional_full_dp_cell_count": total_dp_cells,
            "release_counts": dict(sorted(release_counts.items())),
            "scope_counts": dict(sorted(scope_counts.items())),
            "discovery_channel_counts": dict(sorted(channel_counts.items())),
            "exact_by_release": dict(sorted(exact_by_release.items())),
            "nonexact_by_release": dict(sorted(nonexact_by_release.items())),
            "target_global_status_counts": dict(sorted(target_status_counts.items())),
            "candidate_global_status_counts": dict(
                sorted(candidate_status_counts.items())
            ),
            "best_direction_counts": dict(sorted(best_direction_counts.items())),
            "best_direction_by_channel": {
                "|".join(k): v for k, v in sorted(best_direction_by_channel.items())
            },
            "negative_score_counts": dict(sorted(negative_counts.items())),
            "schema_column_count": len(EXPECTED_FIELDS),
            "policy": {
                "alignment_model": "directional glocal affine full DP",
                "target_global_semantics": (
                    "complete target consumed; candidate terminal overhangs free"
                ),
                "candidate_global_semantics": (
                    "complete candidate consumed; target terminal overhangs free"
                ),
                "nonexact_alignment_is_exact_full_dp": True,
                "banding_used": False,
                "exact_pairs_dp_bypassed": True,
                "exact_pair_facts_synthesized_mathematically": True,
                "same_entity_decision_made_here": False,
                "overlap_06B_is_not_canonical_alignment": True,
            },
        }

        with sum_tmp.open("w", encoding="utf-8") as sh:
            json.dump(summary, sh, indent=2, sort_keys=True)
            sh.write("\n")

        out_tmp.replace(args.output)
        sum_tmp.replace(args.summary)

    except Exception:
        if out_tmp.exists():
            out_tmp.unlink()
        if sum_tmp.exists():
            sum_tmp.unlink()
        raise

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
