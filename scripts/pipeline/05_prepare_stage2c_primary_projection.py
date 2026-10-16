#!/usr/bin/env python3
"""Prepare read-only Stage 2C primary candidates; never publish canonical 05A2."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from probe.stage2c_primary_projection import prepare_primary_projection


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True)
    parser.add_argument("--canonical-05a2", type=Path, required=True)
    parser.add_argument("--bridge", type=Path, required=True)
    parser.add_argument("--target-taxonomy", type=Path, required=True)
    parser.add_argument("--prepared-targets", type=Path, required=True)
    parser.add_argument("--full-metadata-dir", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = prepare_primary_projection(
        release=args.release,
        canonical_05a2=args.canonical_05a2,
        bridge=args.bridge,
        target_taxonomy=args.target_taxonomy,
        prepared_targets=args.prepared_targets,
        full_metadata_dir=args.full_metadata_dir,
        output_dir=args.output_dir,
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
