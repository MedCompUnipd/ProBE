#!/usr/bin/env python3
"""Attest B3 and shared documentary candidates, separated by release."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from probe.canonical_v2.release_candidate_boundary import (
    prepare_release_candidate_boundary,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--b3", type=Path, required=True)
    parser.add_argument("--documentary-handoff", type=Path, required=True)
    parser.add_argument("--start-release", required=True)
    parser.add_argument("--end-release", required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    summary = prepare_release_candidate_boundary(
        b3=args.b3,
        handoff=args.documentary_handoff,
        releases={"START": args.start_release, "END": args.end_release},
        destination=args.destination,
    )
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
