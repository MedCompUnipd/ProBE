#!/usr/bin/env python3
"""Produce the START/END documentary handoff; never approve donor GO."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from probe.canonical_v2.documentary_handoff import prepare_documentary_handoff


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-lookup", required=True, type=Path)
    parser.add_argument("--end-lookup", required=True, type=Path)
    parser.add_argument("--start-release", required=True)
    parser.add_argument("--end-release", required=True)
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    result = prepare_documentary_handoff(
        start_lookup=args.start_lookup,
        end_lookup=args.end_lookup,
        start_release=args.start_release,
        end_release=args.end_release,
        destination=args.destination,
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
