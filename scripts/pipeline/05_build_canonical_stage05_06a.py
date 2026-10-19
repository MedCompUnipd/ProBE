#!/usr/bin/env python3
"""Cold-start Stage 05–06A consumer of the canonical V2 B2c contracts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from probe.canonical_v2.stage05_06a import build_stage05_06a


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    data = json.loads(args.spec.read_text(encoding="utf-8"))
    expected = {
        "releases", "projections", "taxonomies", "anchors",
        "full_manifests", "targets", "stage05c2_cli", "stage06a_cli",
    }
    if set(data) != expected:
        raise ValueError("Canonical Stage 05/06A spec keys mismatch")
    result = build_stage05_06a(
        releases=data["releases"],
        projections={k: Path(v) for k, v in data["projections"].items()},
        taxonomies={k: Path(v) for k, v in data["taxonomies"].items()},
        anchors={k: Path(v) for k, v in data["anchors"].items()},
        full_manifests={
            k: Path(v) for k, v in data["full_manifests"].items()
        },
        targets=Path(data["targets"]),
        stage05c2_cli=Path(data["stage05c2_cli"]),
        stage06a_cli=Path(data["stage06a_cli"]),
        output_dir=args.output_dir,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
