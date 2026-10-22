#!/usr/bin/env python3
"""Execute strict canonical V2 06C with no dependency on legacy Stage 05."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from probe.canonical_v2.alignment_v2 import execute_alignment_v2
from probe.canonical_v2.bounded_dp import BACKEND
from probe.canonical_v2.large_dp import BACKEND as AUTO_BACKEND
from probe.canonical_v2.release_workload import SCHEMA as WORKLOAD_SCHEMA
from probe.stage2c_alignment_completion import _preview


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workload", type=Path, required=True)
    parser.add_argument("--targets", type=Path, required=True)
    parser.add_argument("--start-taxonomy", type=Path, required=True)
    parser.add_argument("--end-taxonomy", type=Path, required=True)
    parser.add_argument("--lineage-index", type=Path, required=True)
    parser.add_argument("--start-lineage", type=Path, required=True)
    parser.add_argument("--end-lineage", type=Path, required=True)
    parser.add_argument("--nonexact-script", type=Path, required=True)
    parser.add_argument("--full-dp-binary", type=Path, required=True)
    parser.add_argument("--linear-dp-binary", type=Path)
    parser.add_argument("--finalize-script", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--max-pair-cells", type=int, default=50_000_000)
    parser.add_argument("--max-total-cells", type=int, default=500_000_000)
    parser.add_argument("--max-chunk-cells", type=int)
    parser.add_argument("--max-dp-memory-bytes", type=int, default=536_870_912)
    parser.add_argument("--dp-checkpoint-rows", type=int, default=256)
    parser.add_argument(
        "--dp-scratch-root",
        type=Path,
        default=Path("/data/PROGENY/tmp/probe-v2-scratch"),
    )
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    summary, _ = _preview(args.workload)
    if summary.get("canonical_contract") != WORKLOAD_SCHEMA:
        raise ValueError("Not a canonical V2 complete workload")
    if not args.execute:
        print(
            json.dumps(
                {
                    "mode": "PREFLIGHT_ONLY",
                    "releases": summary["releases"],
                    "dp_backend": BACKEND,
                    "dp_execution_backend": (
                        AUTO_BACKEND if args.linear_dp_binary else BACKEND
                    ),
                    "alignment_executed": False,
                    "donor_decisions": 0,
                },
                sort_keys=True,
            )
        )
        return
    report = execute_alignment_v2(
        workload=args.workload,
        targets=args.targets,
        taxonomy={
            "START": args.start_taxonomy,
            "END": args.end_taxonomy,
        },
        lineage_index=args.lineage_index,
        lineage_paths={
            summary["releases"]["START"]: args.start_lineage,
            summary["releases"]["END"]: args.end_lineage,
        },
        nonexact_script=args.nonexact_script,
        full_dp_binary=args.full_dp_binary,
        finalize_script=args.finalize_script,
        destination=args.destination,
        threads=args.threads,
        max_pair_cells=args.max_pair_cells,
        max_total_cells=args.max_total_cells,
        max_chunk_cells=args.max_chunk_cells,
        dp_backend=BACKEND,
        linear_dp_binary=args.linear_dp_binary,
        max_dp_memory_bytes=args.max_dp_memory_bytes,
        dp_checkpoint_rows=args.dp_checkpoint_rows,
        dp_scratch_root=args.dp_scratch_root,
    )
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
