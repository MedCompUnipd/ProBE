"""
ProBE production Stage 04B: classify direct accession anchors against the
authoritative release-specific NCBI taxonomy.

This stage does NOT generate lineage keys and does NOT make a SAME_ENTITY
decision. It only decides whether a direct anchor is taxonomically eligible
to act as a SOURCE OF LINEAGE KEYS.

Supported taxonomy policies:

  rank_aware:
    target at species rank -> anchor must share the same species anchor
    target below species   -> anchor must have the same resolved TaxID

  strict_taxon:
    every target           -> anchor must have the same resolved TaxID

All anchors, including cross-species, unresolved, multi-OX and FRAG=1 records,
are retained in the output for audit. FRAG=1 is NOT itself a reason to prevent
a direct accession anchor from contributing lineage metadata; fragment status
is preserved for later entity-resolution checks.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

from probe.taxonomy import NcbiTaxonomyIndex, SpeciesAnchorStatus


def load_target_taxonomy(path: Path) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        required = {
            "target_id",
            "raw_taxid",
            "resolved_taxid",
            "resolved_rank",
            "species_taxid",
            "primary_bucket",
        }
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            missing = required.difference(reader.fieldnames or ())
            raise ValueError(f"{path}: missing columns {sorted(missing)}")
        for row in reader:
            tid = row["target_id"]
            if tid in result:
                raise ValueError(f"{path}: duplicate target_id {tid}")
            result[tid] = row
    return result


def classify_anchor(
    index: NcbiTaxonomyIndex,
    target: dict[str, str],
    raw_ox: str,
    taxonomy_policy: str,
) -> dict[str, str]:
    # Full V2 may encode multiple OX TaxIDs as comma-separated raw values.
    ox_values = [x for x in raw_ox.split(",") if x]
    if len(ox_values) != 1:
        return {
            "anchor_resolution_status": "UNRESOLVED",
            "anchor_resolved_taxid": "",
            "anchor_resolved_rank": "",
            "anchor_species_status": "UNRESOLVED",
            "anchor_species_taxid": "",
            "taxonomy_relation": "UNRESOLVED",
            "key_source_eligible": "0",
            "key_source_reason": "MULTIPLE_OR_MISSING_OX",
        }

    ox = ox_values[0]
    anchor = index.species_anchor(ox)
    resolved = anchor.resolution.resolved_taxid or ""
    node = index.node(resolved) if resolved else None
    species = anchor.species_anchor.taxid if anchor.species_anchor is not None else ""

    if not resolved:
        relation = "UNRESOLVED"
    elif resolved == target["resolved_taxid"]:
        relation = "SAME_TAXON"
    elif (
        anchor.status is SpeciesAnchorStatus.RESOLVED
        and species
        and species == target["species_taxid"]
    ):
        relation = "SAME_SPECIES_DIFFERENT_SUBTAXON"
    else:
        relation = "CROSS_SPECIES"

    eligible = False
    reason = ""

    if taxonomy_policy == "strict_taxon":
        expected_taxid = target["resolved_taxid"]

        if not expected_taxid:
            reason = "TARGET_TAXONOMY_UNRESOLVED"
        elif not resolved:
            reason = "UNRESOLVED_TAXONOMY"
        elif resolved == expected_taxid:
            eligible = True
            reason = "ELIGIBLE_SAME_TAXON"
        elif species and species == target["species_taxid"]:
            reason = "SAME_SPECIES_DIFFERENT_TAXON"
        else:
            reason = "CROSS_SPECIES"

    elif taxonomy_policy == "rank_aware":
        bucket = target["primary_bucket"]

        if bucket.startswith("S:"):
            expected_species = bucket[2:]
            if (
                anchor.status is SpeciesAnchorStatus.RESOLVED
                and species == expected_species
            ):
                eligible = True
                reason = "ELIGIBLE_SAME_SPECIES"
            elif not resolved or anchor.status is not SpeciesAnchorStatus.RESOLVED:
                reason = "UNRESOLVED_TAXONOMY"
            else:
                reason = "CROSS_SPECIES"

        elif bucket.startswith("T:"):
            expected_taxid = bucket[2:]
            if resolved == expected_taxid:
                eligible = True
                reason = "ELIGIBLE_SAME_TAXON"
            elif not resolved:
                reason = "UNRESOLVED_TAXONOMY"
            elif species and species == target["species_taxid"]:
                reason = "SAME_SPECIES_WRONG_SUBTAXON"
            else:
                reason = "CROSS_SPECIES"

        else:
            reason = "TARGET_PRIMARY_BUCKET_UNRESOLVED"

    else:
        raise ValueError(f"unsupported taxonomy policy: {taxonomy_policy}")

    return {
        "anchor_resolution_status": anchor.resolution.status.value,
        "anchor_resolved_taxid": resolved,
        "anchor_resolved_rank": node.rank if node is not None else "",
        "anchor_species_status": anchor.status.value,
        "anchor_species_taxid": species,
        "taxonomy_relation": relation,
        "key_source_eligible": "1" if eligible else "0",
        "key_source_reason": reason,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--anchors", type=Path, required=True)
    ap.add_argument("--target-taxonomy", type=Path, required=True)
    ap.add_argument("--taxonomy-index", type=Path, required=True)
    ap.add_argument(
        "--taxonomy-policy",
        choices=("rank_aware", "strict_taxon"),
        required=True,
    )
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--summary", type=Path, required=True)
    args = ap.parse_args()

    for path in (args.anchors, args.target_taxonomy, args.taxonomy_index):
        if not path.is_file():
            raise FileNotFoundError(path)
    for path in (args.output, args.summary):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)

    targets = load_target_taxonomy(args.target_taxonomy)

    reason_counts: Counter[str] = Counter()
    relation_counts: Counter[str] = Counter()
    frag_counts: Counter[str] = Counter()
    direct_match_counts: Counter[str] = Counter()
    eligible_rows = 0
    total_rows = 0
    targets_with_eligible: set[str] = set()
    targets_with_ineligible: set[str] = set()

    with (
        NcbiTaxonomyIndex(args.taxonomy_index) as index,
        args.anchors.open("r", encoding="utf-8", newline="") as inp,
        args.output.open("w", encoding="utf-8", newline="") as out,
    ):
        reader = csv.DictReader(inp, delimiter="\t")
        if reader.fieldnames is None:
            raise ValueError("anchor file has no header")

        extra_fields = [
            "target_raw_taxid",
            "target_resolved_taxid",
            "target_resolved_rank",
            "target_species_taxid",
            "target_primary_bucket",
            "anchor_resolution_status",
            "anchor_resolved_taxid",
            "anchor_resolved_rank",
            "anchor_species_status",
            "anchor_species_taxid",
            "taxonomy_relation",
            "key_source_eligible",
            "key_source_reason",
        ]
        writer = csv.DictWriter(
            out,
            fieldnames=list(reader.fieldnames) + extra_fields,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()

        for row in reader:
            total_rows += 1
            tid = row["target_id"]
            target = targets.get(tid)
            if target is None:
                raise ValueError(f"anchor references unknown target_id {tid}")

            c = classify_anchor(
                index,
                target,
                row["raw_ox"],
                args.taxonomy_policy,
            )
            row.update(
                {
                    "target_raw_taxid": target["raw_taxid"],
                    "target_resolved_taxid": target["resolved_taxid"],
                    "target_resolved_rank": target["resolved_rank"],
                    "target_species_taxid": target["species_taxid"],
                    "target_primary_bucket": target["primary_bucket"],
                    **c,
                }
            )
            writer.writerow(row)

            reason_counts[c["key_source_reason"]] += 1
            relation_counts[c["taxonomy_relation"]] += 1
            frag_counts[row["frag"]] += 1
            direct_match_counts[row["direct_match_type"]] += 1

            if c["key_source_eligible"] == "1":
                eligible_rows += 1
                targets_with_eligible.add(tid)
            else:
                targets_with_ineligible.add(tid)

    summary = {
        "stage": "04B",
        "taxonomy_policy": args.taxonomy_policy,
        "anchor_row_count": total_rows,
        "eligible_key_source_row_count": eligible_rows,
        "ineligible_key_source_row_count": total_rows - eligible_rows,
        "targets_with_eligible_anchor": len(targets_with_eligible),
        "targets_with_ineligible_anchor": len(targets_with_ineligible),
        "key_source_reason_counts": dict(sorted(reason_counts.items())),
        "taxonomy_relation_counts": dict(sorted(relation_counts.items())),
        "fragment_flag_counts": dict(sorted(frag_counts.items())),
        "direct_match_type_counts": dict(sorted(direct_match_counts.items())),
        "policy": {
            "species_level_target_requires_same_species_anchor": (
                args.taxonomy_policy == "rank_aware"
            ),
            "below_species_target_requires_same_resolved_taxid": True,
            "all_targets_require_same_resolved_taxid": (
                args.taxonomy_policy == "strict_taxon"
            ),
            "cross_species_anchor_generates_keys": False,
            "same_species_wrong_subtaxon_generates_keys": False,
            "unresolved_anchor_generates_keys": False,
            "fragment_direct_anchor_may_generate_keys_if_taxonomically_eligible": True,
            "same_entity_decision_made_here": False,
        },
    }

    with args.summary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)
        handle.write("\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
