#!/usr/bin/env python3
"""Create or verify a pinned Stage 2C Snakemake integration plan."""

from __future__ import annotations

import argparse
from pathlib import Path

from probe.stage2c_workflow_plan import create_plan, read_plan


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--union-spec", type=Path)
    parser.add_argument("--verify-plan", type=Path)
    args = parser.parse_args()
    if (args.union_spec is None) == (args.verify_plan is None):
        parser.error("Specify exactly one of --union-spec or --verify-plan")
    if args.verify_plan is not None:
        result = read_plan(args.verify_plan)
        if result["repo"] != str(args.repo.resolve(strict=True)):
            raise ValueError("Wrong repository")
        print(f"STAGE2C_INTEGRATION_PLAN_VERIFIED={result['key']}")
    else:
        path = create_plan(args.repo, args.union_spec)
        print(f"STAGE2C_INTEGRATION_PLAN={path}")
        print(f"STAGE2C_INTEGRATION_KEY={read_plan(path)['key']}")


if __name__ == "__main__":
    main()
