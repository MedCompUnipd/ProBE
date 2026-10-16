#!/usr/bin/env python3
"""Reconcile FULL metadata/sequence inputs without donor decisions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from probe.stage2c_metadata_reconciliation import reconcile_full_metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True)
    parser.add_argument("--requests", required=True, type=Path)
    parser.add_argument("--reuse-dir", required=True, type=Path)
    parser.add_argument("--canonical-05d-store", required=True, type=Path)
    parser.add_argument("--recovery-dir", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    result = reconcile_full_metadata(
        release=args.release,
        requests=args.requests,
        reuse_dir=args.reuse_dir,
        canonical_05d_store=args.canonical_05d_store,
        recovery_dir=args.recovery_dir,
        output_dir=args.output_dir,
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
