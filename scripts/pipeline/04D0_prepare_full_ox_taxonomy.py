#!/usr/bin/env python3
"""
Production ProBE candidate pipeline.

Stage 04D0b: resolve every distinct raw OX token occurring in a FULL enriched
UniProt V2 release, using the already-built authoritative NCBI taxonomy index.

This exists because Stage 02 OX maps were built from the GO-positive search
universe, whereas lineage rescue deliberately scans the FULL V2 release.

No taxonomy semantics are implemented independently here: single TaxIDs are
resolved through probe.taxonomy.NcbiTaxonomyIndex.  Multi-OX and malformed
tokens are conservatively marked unresolved and receive no candidate bucket.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from probe.taxonomy import NcbiTaxonomyIndex, SpeciesAnchorStatus


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ox-list", type=Path, required=True)
    ap.add_argument("--taxonomy-index", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--summary", type=Path, required=True)
    args = ap.parse_args()

    for path in (args.ox_list, args.taxonomy_index):
        if not path.is_file():
            raise FileNotFoundError(path)
    for path in (args.output, args.summary):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)

    raw_class_counts: Counter[str] = Counter()
    resolution_counts: Counter[str] = Counter()
    species_counts: Counter[str] = Counter()
    total = 0

    with (
        NcbiTaxonomyIndex(args.taxonomy_index) as index,
        args.ox_list.open("r", encoding="utf-8") as inp,
        args.output.open("w", encoding="utf-8", newline="\n") as out,
    ):
        out.write(
            "raw_ox\traw_ox_class\tresolution_status\tresolved_taxid"
            "\tresolved_rank\tmerge_path\tlineage_status"
            "\tspecies_status\tspecies_taxid"
            "\tspecies_bucket\tresolved_bucket\n"
        )

        for raw_line in inp:
            raw = raw_line.rstrip("\n\r")
            if not raw:
                continue
            total += 1

            raw_class = ""
            resolution_status = ""
            resolved_taxid = ""
            resolved_rank = ""
            merge_path = ""
            lineage_status = ""
            species_status = ""
            species_taxid = ""
            species_bucket = ""
            resolved_bucket = ""

            if "," in raw:
                raw_class = "MULTIPLE_OX"
                resolution_status = "UNRESOLVED"
                lineage_status = "UNRESOLVED"
                species_status = "UNRESOLVED"
            elif not raw.isdigit():
                raw_class = "MALFORMED_OX"
                resolution_status = "UNRESOLVED"
                lineage_status = "UNRESOLVED"
                species_status = "UNRESOLVED"
            else:
                raw_class = "SINGLE_TAXID"
                anchor = index.species_anchor(raw)
                resolution_status = anchor.resolution.status.value
                resolved_taxid = anchor.resolution.resolved_taxid or ""
                merge_path = ",".join(anchor.resolution.merge_path)
                lineage_status = anchor.lineage.status.value
                species_status = anchor.status.value

                if resolved_taxid:
                    node = index.node(resolved_taxid)
                    resolved_rank = node.rank if node is not None else ""
                    resolved_bucket = f"T:{resolved_taxid}"

                if (
                    anchor.status is SpeciesAnchorStatus.RESOLVED
                    and anchor.species_anchor is not None
                ):
                    species_taxid = anchor.species_anchor.taxid
                    species_bucket = f"S:{species_taxid}"

            raw_class_counts[raw_class] += 1
            resolution_counts[resolution_status] += 1
            species_counts[species_status] += 1

            out.write(
                "\t".join(
                    [
                        raw,
                        raw_class,
                        resolution_status,
                        resolved_taxid,
                        resolved_rank,
                        merge_path,
                        lineage_status,
                        species_status,
                        species_taxid,
                        species_bucket,
                        resolved_bucket,
                    ]
                )
                + "\n"
            )

    summary = {
        "stage": "04D0b",
        "distinct_raw_ox_count": total,
        "raw_ox_class_counts": dict(sorted(raw_class_counts.items())),
        "resolution_status_counts": dict(sorted(resolution_counts.items())),
        "species_status_counts": dict(sorted(species_counts.items())),
        "policy": {
            "single_taxid_resolution_uses_probe_taxonomy": True,
            "multiple_ox_receives_candidate_bucket": False,
            "malformed_ox_receives_candidate_bucket": False,
        },
    }

    with args.summary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)
        handle.write("\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
