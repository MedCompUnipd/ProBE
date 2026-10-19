#!/usr/bin/env python3
"""Generate read-only EXACT-derived documentary observations for V2."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from probe.canonical_v2.exact_documentary_registry import (
    build_exact_documentary_register,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True)
    parser.add_argument("--discovery", type=Path, required=True)
    parser.add_argument("--targets", type=Path, required=True)
    parser.add_argument("--full-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    summary = build_exact_documentary_register(
        release=args.release,
        discovery=args.discovery,
        targets=args.targets,
        full_manifest=args.full_manifest,
        output_dir=args.output_dir,
    )
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
