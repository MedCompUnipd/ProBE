#!/usr/bin/env python3
"""Produce explicit EVAL/SKIP/NEUTRAL masks from immutable M10c evidence.

Runs metadata preflight by default; requires --execute to publish a new bundle.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from probe.stage08_temporal_benchmark import file_sha256, inspect_m10c_bundle
from probe.stage08_temporal_v2 import build_temporal_benchmark_v2


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--m10c-bundle", type=Path, required=True)
    parser.add_argument("--m8-summary", type=Path, required=True)
    parser.add_argument("--targets-input", type=Path, required=True)
    parser.add_argument("--stage01-targets", type=Path, required=True)
    parser.add_argument("--evidence-registry", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--ontology", type=Path, required=True)
    parser.add_argument("--start-release", required=True)
    parser.add_argument("--end-release", required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    _, manifest = inspect_m10c_bundle(args.m10c_bundle)
    if args.ontology.is_symlink() or not args.ontology.is_file():
        raise ValueError("Pinned ontology must be a regular file")
    if file_sha256(args.ontology) != manifest["ontology_sha256"]:
        raise ValueError("Pinned ontology differs from M10c")
    result = build_temporal_benchmark_v2(
        m10c_bundle=args.m10c_bundle,
        m8_summary=args.m8_summary,
        targets_input=args.targets_input,
        stage01_targets=args.stage01_targets,
        registry_path=args.evidence_registry,
        policy_path=args.policy,
        destination=args.destination,
        start_release=args.start_release,
        end_release=args.end_release,
        execute=args.execute,
        implementation_script=Path(__file__) if args.execute else None,
    )
    print(json.dumps(result, sort_keys=True))
    print(
        "M11_M12_V2_PUBLISHED=YES"
        if result["output_published"]
        else "M11_M12_V2_PREFLIGHT_PASS=YES"
    )


if __name__ == "__main__":
    main()
