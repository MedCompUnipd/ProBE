#!/usr/bin/env python3
"""Prepare complete two-lane 06A workload without legacy Stage 05 publications."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from probe.canonical_v2.release_workload import build_release_workload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    specification = json.loads(args.spec.read_text(encoding="utf-8"))
    expected = {
        "b3", "handoff", "releases", "targets", "full_manifests",
        "stage06a_cli",
    }
    if set(specification) != expected:
        raise ValueError("Unexpected canonical workload specification keys")
    result = build_release_workload(
        b3=Path(specification["b3"]),
        handoff=Path(specification["handoff"]),
        releases=specification["releases"],
        targets=Path(specification["targets"]),
        full_manifests={
            role: Path(path)
            for role, path in specification["full_manifests"].items()
        },
        stage06a_cli=Path(specification["stage06a_cli"]),
        destination=args.destination,
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
