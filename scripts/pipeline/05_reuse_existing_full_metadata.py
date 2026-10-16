#!/usr/bin/env python3
"""Reuse already published Stage 05D metadata without scanning UniProt."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from probe.stage2c_metadata_reuse import reuse_05d_metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True)
    parser.add_argument("--requests", required=True, type=Path)
    parser.add_argument("--store-05d", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    result = reuse_05d_metadata(
        release=args.release,
        requests=args.requests,
        store_05d=args.store_05d,
        output_dir=args.output_dir,
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
