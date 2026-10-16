#!/usr/bin/env python3
"""Prepare isolated Stage 2C alignment inputs; never alter canonical stages."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from probe.stage2c_alignment_preview import build_alignment_preview


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-release", required=True)
    parser.add_argument("--end-release", required=True)
    for name in (
        "canonical-05a2",
        "canonical-05c2",
        "canonical-start-05d",
        "canonical-end-05d",
        "projection-start",
        "projection-end",
        "coverage-start",
        "coverage-end",
        "recovered-go-start",
        "recovered-go-end",
        "targets",
        "stage06a-script",
        "output-dir",
    ):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument(
        "--execute-preview",
        action="store_true",
        help="Read verified inputs and build isolated evidence previews",
    )
    args = parser.parse_args()
    paths = {
        key: value
        for key, value in vars(args).items()
        if key not in {"start_release", "end_release", "execute_preview"}
    }
    if not args.execute_preview:
        print("STAGE_2C4D_PREFLIGHT_ONLY")
        print("No scientific files were read or produced")
        return 0
    result = build_alignment_preview(
        start_release=args.start_release,
        end_release=args.end_release,
        **paths,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
