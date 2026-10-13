"""Create or verify metadata for the independent species-wide Stage 03 DAG."""

from __future__ import annotations

import argparse
from pathlib import Path

from probe.stage2c_discovery import create_plan, read_plan


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("create", "verify"))
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--plan", type=Path)
    args = parser.parse_args()
    if args.command == "create":
        path = create_plan(args.repo, args.run)
        read_plan(path)
        print(f"DISCOVERY_PLAN={path}")
    else:
        if args.plan is None:
            parser.error("--plan is required for verify")
        plan = read_plan(args.plan)
        if plan["repo"] != str(args.repo.resolve(strict=True)):
            raise ValueError("Repository mismatch")
        if plan["run"] != str(args.run.resolve(strict=True)):
            raise ValueError("Run mismatch")
        print(f"DISCOVERY_PLAN_VERIFIED={plan['key']}")


if __name__ == "__main__":
    main()
