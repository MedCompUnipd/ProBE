"""Prepare strict canonical external targets for the ProBE pipeline."""

from __future__ import annotations

import argparse
from pathlib import Path

from probe.target_preparation import prepare_targets


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-fasta", type=Path, required=True)
    parser.add_argument("--target-metadata", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    arguments = parser.parse_args(argv)
    result = prepare_targets(
        target_fasta=arguments.target_fasta,
        metadata_tsv=arguments.target_metadata,
        output_directory=arguments.output_directory,
    )
    print(f"accepted targets: {result.accepted_count}")
    print(f"rejected targets: {result.rejected_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
