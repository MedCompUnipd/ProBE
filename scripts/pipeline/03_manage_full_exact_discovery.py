"""Create and verify immutable Stage 2C FULL exact-discovery plans."""

from __future__ import annotations

import argparse
from pathlib import Path

from probe.stage2c_full_exact import create_plan, read_plan


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("create", "verify"))
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--plan", type=Path)
    args = parser.parse_args()
    if args.command == "create":
        path = create_plan(args.repo, args.run)
        read_plan(path)
        print(f"FULL_EXACT_PLAN={path}")
        return
    if args.plan is None:
        parser.error("--plan is required for verify")
    plan = read_plan(args.plan)
    if plan["repo"] != str(args.repo.resolve(strict=True)):
        raise ValueError("Repository differs from pinned plan")
    if plan["run"] != str(args.run.resolve(strict=True)):
        raise ValueError("Run differs from pinned plan")
    print(f"FULL_EXACT_PLAN_VERIFIED={plan['key']}")


if __name__ == "__main__":
    main()
