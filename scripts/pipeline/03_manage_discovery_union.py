#!/usr/bin/env python3
"""Create or verify an immutable, policy-independent Stage 2C union plan."""

from __future__ import annotations

import argparse
from pathlib import Path

from probe.stage2c_union_plan import create_plan, read_plan


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--species-plan", required=True, type=Path)
    parser.add_argument("--full-plan", required=True, type=Path)
    args = parser.parse_args()
    path = create_plan(args.repo, args.species_plan, args.full_plan)
    spec = read_plan(path)
    print(f"UNION_PLAN={path}")
    print(f"UNION_KEY={spec['key']}")
    print("UNION_PLAN_VERIFIED=PASS")


if __name__ == "__main__":
    main()
