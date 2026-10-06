"""Compile the GO normalization policy for the ProBE pipeline."""

from __future__ import annotations

import argparse
from pathlib import Path

from probe.go_policy import compile_go_policy


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owl", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-owl-sha256")
    parser.add_argument("--overwrite", action="store_true")
    arguments = parser.parse_args(argv)

    result = compile_go_policy(
        owl_path=arguments.owl,
        output_directory=arguments.output_dir,
        expected_owl_sha256=arguments.expected_owl_sha256,
        overwrite=arguments.overwrite,
    )

    print(f"policy:   {result.policy_path}")
    print(f"stats:    {result.stats_path}")
    print(f"manifest: {result.scientific_manifest_path}")
    print(f"rows:     {result.policy_row_count:,}")
    print(f"usable:   {result.usable_resolution_count:,}")
    print(f"remapped: {result.remapped_resolution_count:,}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
