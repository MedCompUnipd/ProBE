#!/usr/bin/env python3
"""Publish NONEXACT discovery from independent and inherited typed keys."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from probe.canonical_v2.documentary_lookup import discover_documentary_nonexact


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True)
    parser.add_argument("--targets", type=Path, required=True)
    parser.add_argument("--target-taxonomy", type=Path, required=True)
    parser.add_argument("--exact-register", type=Path, required=True)
    parser.add_argument("--other-exact-register", type=Path, required=True)
    parser.add_argument("--other-release", required=True)
    parser.add_argument("--independent-index", type=Path, required=True)
    parser.add_argument("--ox-taxonomy", type=Path, required=True)
    parser.add_argument("--full-manifest", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    arguments = vars(parser.parse_args())
    print(json.dumps(discover_documentary_nonexact(**arguments), sort_keys=True))


if __name__ == "__main__":
    main()
