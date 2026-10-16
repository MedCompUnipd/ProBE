#!/usr/bin/env python3
"""Preflight or explicitly scan pinned 01A_FULL for pending Stage 2C RIDs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from probe.stage2c_full_metadata_recovery import (
    preflight_full_recovery,
    recover_pending_full_metadata,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True)
    parser.add_argument("--pending-requests", type=Path, required=True)
    parser.add_argument("--full-v2-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--execute-full-scan", action="store_true")
    args = parser.parse_args()
    if args.execute_full_scan:
        if args.output_dir is None:
            parser.error("--output-dir is mandatory for --execute-full-scan")
        result = recover_pending_full_metadata(
            release=args.release,
            pending_requests=args.pending_requests,
            full_v2_manifest=args.full_v2_manifest,
            output_dir=args.output_dir,
        )
    else:
        if args.output_dir is not None:
            parser.error("--output-dir requires --execute-full-scan")
        result = preflight_full_recovery(
            release=args.release,
            pending_requests=args.pending_requests,
            full_v2_manifest=args.full_v2_manifest,
        )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
