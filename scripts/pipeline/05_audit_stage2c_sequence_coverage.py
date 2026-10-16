#!/usr/bin/env python3
"""Audit Stage 2C new-candidate sequence availability without modifying 05D."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from probe.stage2c_sequence_coverage import build_sequence_coverage


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True)
    parser.add_argument("--projection-dir", type=Path, required=True)
    parser.add_argument("--canonical-05d-store", type=Path, required=True)
    parser.add_argument("--prepared-targets", type=Path, required=True)
    parser.add_argument("--full-metadata-dir", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = build_sequence_coverage(
        release=args.release,
        projection_dir=args.projection_dir,
        store_05d=args.canonical_05d_store,
        prepared_targets=args.prepared_targets,
        full_metadata_dir=args.full_metadata_dir,
        output_dir=args.output_dir,
    )
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
