#!/usr/bin/env python3
"""Explicit, opt-in merge of two already published Stage 2C scan products."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from probe.stage2c_candidate_union import merge_discovery


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True)
    parser.add_argument("--target-taxonomy", type=Path, required=True)
    parser.add_argument("--go-positive-pairs", type=Path, required=True)
    parser.add_argument("--full-exact-pairs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    paths = (
        args.target_taxonomy,
        args.go_positive_pairs,
        args.full_exact_pairs,
    )
    for path in paths:
        if not path.is_file():
            parser.error(f"Input does not exist: {path}")
    if args.output == args.summary:
        parser.error("Output and summary must be distinct")
    if args.output.exists() or args.summary.exists():
        parser.error("Refusing to overwrite existing output")
    if not args.apply:
        print("DRY_RUN_ONLY: no scan or merge executed")
        return
    result = merge_discovery(
        release=args.release,
        target_taxonomy=args.target_taxonomy,
        go_positive_pairs=args.go_positive_pairs,
        full_exact_pairs=args.full_exact_pairs,
        output=args.output,
        summary=args.summary,
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
