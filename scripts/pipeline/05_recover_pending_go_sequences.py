#!/usr/bin/env python3
"""Recover GO-only candidate sequences from the pinned Stage 01C FASTA."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from probe.stage2c_go_sequence_recovery import (
    preflight_pending_go,
    recover_pending_go,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True)
    parser.add_argument("--coverage-dir", type=Path, required=True)
    parser.add_argument("--go-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--execute-go-scan", action="store_true")
    args = parser.parse_args()
    if not args.execute_go_scan:
        report = preflight_pending_go(
            release=args.release,
            coverage_dir=args.coverage_dir,
            go_manifest=args.go_manifest,
        )
        print(json.dumps(report, sort_keys=True))
        return
    result = recover_pending_go(
        release=args.release,
        coverage_dir=args.coverage_dir,
        go_manifest=args.go_manifest,
        output_dir=args.output_dir,
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
