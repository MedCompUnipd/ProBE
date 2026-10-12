#!/usr/bin/env python3
"""Explicit, fail-closed CLI for the raw Stage 08 GOA donor collection.

Preflight is the default and never opens a normalized GAF. A real collection
requires --execute and cannot overwrite any completed destination. Workflow
invalidation and Snakemake rule wiring are separate future increments.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from probe.stage08_collection_inputs import prepare_collection_inputs
from probe.stage08_collection_publish import publish_raw_annotation_collection

RUNNER_SCHEMA = "stage08-collection-cli-v1"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage07-bundle", type=Path, required=True)
    parser.add_argument("--start-goa-bundle", type=Path, required=True)
    parser.add_argument("--end-goa-bundle", type=Path, required=True)
    parser.add_argument("--go-policy-bundle", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Read both complete GAFs and publish a new immutable bundle",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    inputs = {
        "stage07_bundle": args.stage07_bundle,
        "start_goa_bundle": args.start_goa_bundle,
        "end_goa_bundle": args.end_goa_bundle,
        "go_policy_bundle": args.go_policy_bundle,
    }
    destination = args.destination.absolute()
    if not args.execute:
        prepared = prepare_collection_inputs(**inputs)
        report = {
            "schema_version": RUNNER_SCHEMA,
            "mode": "PREFLIGHT_ONLY",
            "destination": str(destination),
            "normalized_gaf_contents_read": False,
            "output_published": False,
            "collection_input_report": prepared.report(),
        }
    else:
        # Full scan is never implicit. No workflow invalidation override exists
        # here; a valid completed bundle must remain immutable.
        manifest = publish_raw_annotation_collection(
            **inputs,
            destination=destination,
            allow_invalidated_destination=False,
            implementation_sources={
                "scripts/pipeline/08_collect_annotations.py": Path(__file__),
            },
        )
        report = {
            "schema_version": RUNNER_SCHEMA,
            "mode": "EXECUTED",
            "destination": str(destination),
            "bundle_stage": manifest.stage,
            "manifest_status": manifest.status,
            "output_artifact_count": len(manifest.outputs),
            "input_fingerprint": manifest.parameters["input_fingerprint"],
        }
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
