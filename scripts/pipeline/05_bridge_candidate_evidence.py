#!/usr/bin/env python3
"""Build a read-only-input candidate evidence bridge in a new directory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from probe.stage2c_evidence_bridge import prepare_evidence_bridge


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", required=True)
    parser.add_argument("--delta", type=Path, required=True)
    parser.add_argument("--go-positive-pairs", type=Path, required=True)
    parser.add_argument("--full-exact-pairs", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = prepare_evidence_bridge(
        release=args.release,
        delta=args.delta,
        go_positive_pairs=args.go_positive_pairs,
        full_exact_pairs=args.full_exact_pairs,
        output_dir=args.output_dir,
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
