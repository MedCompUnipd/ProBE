#!/usr/bin/env python3
"""Explicit canonical V2 format adapters; no scanning, DP, or GO processing."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from probe.canonical_v2.interop import publish_completion_input
from probe.canonical_v2.stage07_bridge import publish_stage07_adapter


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare-completion", "publish-stage07"):
        option = commands.add_parser(name)
        option.add_argument("--start-release", required=True)
        option.add_argument("--end-release", required=True)
        option.add_argument("--destination", required=True, type=Path)
        if name == "prepare-completion":
            option.add_argument("--b3", required=True, type=Path)
        else:
            option.add_argument("--resolver", required=True, type=Path)
            option.add_argument(
                "--policy",
                choices=("strict", "same_species_exact"),
                default="same_species_exact",
            )
    args = parser.parse_args()
    releases = {"START": args.start_release, "END": args.end_release}
    if args.command == "prepare-completion":
        result = publish_completion_input(
            b3=args.b3,
            releases=releases,
            destination=args.destination,
        )
    else:
        result = publish_stage07_adapter(
            resolver=args.resolver,
            releases=releases,
            policy=args.policy,
            destination=args.destination,
        )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
