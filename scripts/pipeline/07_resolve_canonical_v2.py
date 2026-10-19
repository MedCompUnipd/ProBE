#!/usr/bin/env python3
"""Publish canonical V2 Stage 07 without manufacturing old 05/2C bundles.

Default is read-only preflight. --execute authorizes a new Stage 07 output.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from probe.canonical_v2.resolver_runner import publish_canonical_stage07
from probe.stage2c_alignment_completion import _preview

REQUIRED = frozenset({
    "workload", "completion", "targets", "taxonomy_paths",
    "anchor_paths", "lineage_index", "lineage_paths", "policy",
    "partial_sequence", "documentary_fragment",
})


def _load(path: Path) -> dict:
    if not path.is_file() or path.is_symlink():
        raise ValueError("Invalid canonical Stage 07 specification")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != REQUIRED:
        raise ValueError("Unexpected canonical Stage 07 specification keys")
    if data["policy"] not in {"strict", "same_species_exact"}:
        raise ValueError("Unsupported EXACT donor policy")
    for label in ("taxonomy_paths", "anchor_paths", "lineage_paths"):
        if not isinstance(data[label], dict):
            raise ValueError(f"Invalid {label} mapping")
    if set(data["taxonomy_paths"]) != set(data["anchor_paths"]):
        raise ValueError("Release-local documentary inputs are incomplete")
    if set(data["taxonomy_paths"]) != set(data["lineage_paths"]):
        raise ValueError("Lineage evidence is incomplete")
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    spec = _load(args.spec)
    meta, _ = _preview(Path(spec["workload"]))
    releases = meta["releases"]
    if set(releases.values()) != set(spec["taxonomy_paths"]):
        raise ValueError("Resolver specification release IDs disagree")
    if not args.execute:
        print(json.dumps({
            "mode": "PREFLIGHT_ONLY", "releases": releases,
            "policy": spec["policy"], "donor_decisions": 0,
        }, sort_keys=True))
        return
    result = publish_canonical_stage07(
        workload=Path(spec["workload"]),
        completion=Path(spec["completion"]),
        targets=Path(spec["targets"]),
        taxonomy_paths={
            key: Path(value) for key, value in spec["taxonomy_paths"].items()
        },
        anchor_paths={
            key: Path(value) for key, value in spec["anchor_paths"].items()
        },
        lineage_index=Path(spec["lineage_index"]),
        lineage_paths={
            key: Path(value) for key, value in spec["lineage_paths"].items()
        },
        policy=spec["policy"],
        partial_sequence=spec["partial_sequence"],
        documentary_fragment=spec["documentary_fragment"],
        destination=args.destination,
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
