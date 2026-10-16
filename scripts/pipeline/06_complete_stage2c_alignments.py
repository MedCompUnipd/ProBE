#!/usr/bin/env python3
"""Isolated Stage 2C4D2 preview completion using production 06C code."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from probe.stage2c_alignment_completion import complete_alignment_preview


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview", required=True, type=Path)
    parser.add_argument("--targets", required=True, type=Path)
    parser.add_argument("--work-root", required=True, type=Path)
    parser.add_argument("--canonical-primary", required=True, type=Path)
    parser.add_argument("--nonexact-script", required=True, type=Path)
    parser.add_argument("--full-dp-binary", required=True, type=Path)
    parser.add_argument("--finalize-script", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--max-pair-cells", type=int, default=50_000_000)
    parser.add_argument("--max-total-cells", type=int, default=500_000_000)
    parser.add_argument("--max-chunk-cells", type=int, default=None)
    parser.add_argument("--execute-alignments", action="store_true")
    args = parser.parse_args()
    paths = (
        args.preview,
        args.targets,
        args.canonical_primary,
        args.work_root / "START/02B/02B_target_taxonomy.tsv",
        args.work_root / "END/02B/02B_target_taxonomy.tsv",
        args.nonexact_script,
        args.full_dp_binary,
        args.finalize_script,
    )
    if not all(path.exists() for path in paths):
        raise FileNotFoundError("One or more alignment inputs are missing")
    if not args.execute_alignments:
        print("PREFLIGHT_ONLY: no alignment execution or publication")
        return 0
    result = complete_alignment_preview(
        preview=args.preview,
        targets=args.targets,
        work_root=args.work_root,
        canonical_primary=args.canonical_primary,
        nonexact_script=args.nonexact_script,
        full_dp_binary=args.full_dp_binary,
        finalize_script=args.finalize_script,
        output_dir=args.output_dir,
        threads=args.threads,
        max_pair_cells=args.max_pair_cells,
        max_total_cells=args.max_total_cells,
        max_chunk_cells=args.max_chunk_cells,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
