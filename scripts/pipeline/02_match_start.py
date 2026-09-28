"""Stream START UniProt DAT sources to find strict exact target matches."""

from __future__ import annotations

import argparse
from pathlib import Path

from probe.records import UniProtSection
from probe.release_matching import match_prepared_targets
from probe.taxonomy import NcbiTaxonomyIndex


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared-targets", type=Path, required=True)
    parser.add_argument("--start-swiss-prot", type=Path, required=True)
    parser.add_argument("--start-trembl", type=Path, required=True)
    parser.add_argument("--start-taxonomy-index", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    arguments = parser.parse_args(argv)
    with NcbiTaxonomyIndex(arguments.start_taxonomy_index) as taxonomy:
        result = match_prepared_targets(
            prepared_targets_tsv=arguments.prepared_targets,
            sources={
                UniProtSection.SWISS_PROT: arguments.start_swiss_prot,
                UniProtSection.TREMBL: arguments.start_trembl,
            },
            taxonomy=taxonomy,
            output_directory=arguments.output_directory,
        )
    print(f"strict START matches: {result.admitted_matches}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
