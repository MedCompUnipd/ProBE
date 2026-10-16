#!/usr/bin/env python3
"""Reconcile Stage 2C discovery with a frozen Stage 05A2 candidate universe."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from probe.stage2c_candidate_delta import reconcile_candidate_delta


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True)
    parser.add_argument("--legacy-union", required=True, type=Path)
    parser.add_argument("--discovery-union", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    args = parser.parse_args()
    result = reconcile_candidate_delta(
        release=args.release,
        legacy_union=args.legacy_union,
        discovery_union=args.discovery_union,
        output=args.output,
        summary=args.summary,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
