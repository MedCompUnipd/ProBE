#!/usr/bin/env python3
"""Profile a published ProBE benchmark without modifying its inputs.

Outputs are descriptive, not a new benchmark or CAFA evaluator.
Information accretion requires independent, frozen GO-term weights.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import shutil
import statistics
import uuid
from collections import Counter, defaultdict
from pathlib import Path

ASPECTS = ("F", "P", "C")
STATUSES = ("EVAL", "SKIP", "NEUTRAL")
DEFAULT_EDGES = (0, 300, 600, 1023, 1500, 3000)
IA_EDGES = (0.0, 1.0, 2.0, 4.0, 8.0)


def tsv_rows(path: Path):
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if not reader.fieldnames:
            raise ValueError(f"Empty TSV: {path}")
        yield from reader


def write_tsv(path: Path, columns: list[str], rows: list[dict]):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=columns,
            delimiter="\t",
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def parse_edges(value: str) -> tuple[int, ...]:
    edges = tuple(int(item.strip()) for item in value.split(","))
    if len(edges) < 2 or edges[0] != 0:
        raise ValueError("Length bin edges must start at 0 and have >=2 items")
    if any(left >= right for left, right in zip(edges, edges[1:], strict=False)):
        raise ValueError("Length bin edges must be strictly increasing")
    return edges


def length_bin(length: int, edges: tuple[int, ...]) -> str:
    for index in range(len(edges) - 1):
        if edges[index] <= length < edges[index + 1]:
            return f"{edges[index]}-{edges[index + 1] - 1}"
    return f"{edges[-1]}+"


def ia_bin(value: float) -> str:
    if value == 0:
        return "0"
    for low, high in zip(IA_EDGES, IA_EDGES[1:], strict=False):
        if low < value <= high:
            return f"({low:g},{high:g}]"
    return f">{IA_EDGES[-1]:g}"


def load_weights(path: Path) -> dict[str, float]:
    weights = {}
    for row in tsv_rows(path):
        columns = {key.strip().lower(): key for key in row}
        id_column = next(
            (columns[key] for key in ("go_id", "term", "go") if key in columns),
            None,
        )
        ia_column = next(
            (columns[key] for key in ("ia", "information_accretion") if key in columns),
            None,
        )
        if id_column is None or ia_column is None:
            raise ValueError("IA TSV needs go_id/term and ia columns")
        term = row[id_column].strip()
        weight = float(row[ia_column])
        if not term.startswith("GO:") or not math.isfinite(weight):
            raise ValueError(f"Invalid IA record: {row}")
        if weight < 0 or term in weights:
            raise ValueError(f"Negative/duplicate IA term: {term}")
        weights[term] = weight
    if not weights:
        raise ValueError("No IA weights found")
    return weights


def load_taxon_names(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    names = {}
    for row in tsv_rows(path):
        name = row.get("scientific_name", row.get("name", ""))
        names[row["taxid"]] = name
    return names


def load_target_metadata(
    targets: Path,
    stage01: Path,
) -> tuple[dict[str, tuple[str, int]], dict[str, int]]:
    """Require matching IDs and safe collapse of identical accession aliases."""
    input_map = {}
    for row in tsv_rows(targets):
        target_id = row["target_id"]
        if target_id in input_map:
            raise ValueError(f"Duplicate input target_id: {target_id}")
        input_map[target_id] = (
            row["uniprot_accession"],
            row["ncbi_taxid"],
        )
    by_accession = {}
    checked = 0
    duplicate_aliases = 0
    for row in tsv_rows(stage01):
        target_id = row["target_id"]
        if target_id not in input_map:
            raise ValueError(f"Stage01 ID absent from input: {target_id}")
        accession, taxid = input_map.pop(target_id)
        if (row["optional_accession"], row["raw_taxid"]) != (accession, taxid):
            raise ValueError(f"Stage01 identity mismatch: {target_id}")
        length = int(row["sequence_length"])
        if length <= 0:
            raise ValueError(f"Invalid length: {target_id}")
        identity = (
            taxid,
            length,
            row["sequence_sha256"],
            row["normalization_policy_version"],
        )
        previous = by_accession.get(accession)
        if previous is not None:
            if previous[1] != identity:
                raise ValueError(f"Unsafe accession collision: {accession}")
            duplicate_aliases += 1
        else:
            by_accession[accession] = ((taxid, length), identity)
        checked += 1
    if input_map:
        raise ValueError(f"Unmatched input target IDs: {len(input_map)}")
    return (
        {key: pair[0] for key, pair in by_accession.items()},
        {"stage01_targets": checked, "identical_aliases": duplicate_aliases},
    )


def percentile(sorted_values: list[int], percentage: float) -> float:
    if not sorted_values:
        return 0.0
    pos = (len(sorted_values) - 1) * percentage
    lower = int(pos)
    upper = min(lower + 1, len(sorted_values) - 1)
    return sorted_values[lower] * (1 - (pos - lower)) + (
        sorted_values[upper] * (pos - lower)
    )


def probe(args: argparse.Namespace) -> dict:
    root = args.benchmark
    required = (
        "benchmark_terms.tsv",
        "target_aspects.tsv",
        "term_audit.tsv",
        "manifest.json",
    )
    for filename in required:
        if not (root / filename).is_file():
            raise FileNotFoundError(root / filename)
    if args.output.exists():
        raise FileExistsError(f"Output exists: {args.output}. Choose a new directory.")
    args.output.mkdir()

    metadata, identity = load_target_metadata(args.targets, args.stage01)
    selected_taxa = set(args.taxids.split(",")) if args.taxids else None
    eligible = set()
    declared_counts = {}
    proteins = {}
    for row in tsv_rows(root / "target_aspects.tsv"):
        accession = row["query_id"]
        aspect = row["aspect"]
        if aspect not in ASPECTS or accession not in metadata:
            raise ValueError(f"Invalid eligible target/aspect: {row}")
        taxid, length = metadata[accession]
        if args.min_length is not None and length < args.min_length:
            continue
        if args.max_length is not None and length > args.max_length:
            continue
        if selected_taxa is not None and taxid not in selected_taxa:
            continue
        key = (accession, aspect)
        if key in eligible:
            raise ValueError(f"Duplicate eligible target/aspect: {key}")
        eligible.add(key)
        declared_counts[key] = {
            status: int(row[f"{status.lower()}_terms"])
            for status in STATUSES
            if f"{status.lower()}_terms" in row
        }
        proteins.setdefault(
            accession,
            {
                "taxid": taxid,
                "length": length,
                "aspects": set(),
                "counts": Counter(),
                "eval_ia_sum": 0.0,
            },
        )["aspects"].add(aspect)

    if not eligible:
        raise ValueError("No eligible proteins after filtering")
    weights = load_weights(args.ia) if args.ia else None
    taxon_names = load_taxon_names(args.taxon_names)
    total = Counter()
    per_aspect = Counter()
    per_go = Counter()
    unique_go = defaultdict(set)
    per_length = Counter()
    per_taxon = Counter()
    per_length_go = defaultdict(set)
    per_taxon_go = defaultdict(set)
    per_length_reason = Counter()
    per_taxon_reason = Counter()
    ia_hist = Counter()
    ia_unique_terms = defaultdict(set)
    ia_stats = defaultdict(lambda: [0, 0, 0.0])
    length_ia = defaultdict(lambda: [0, 0, 0.0])
    encountered_go = set()

    for row in tsv_rows(root / "benchmark_terms.tsv"):
        accession = row["query_id"]
        aspect = row["aspect"]
        status = row["status"]
        go_id = row["go_id"]
        if (accession, aspect) not in eligible:
            continue
        if status not in STATUSES:
            raise ValueError(f"Unsupported benchmark status: {status}")
        info = proteins[accession]
        bin_label = length_bin(info["length"], args.length_edges)
        total[status] += 1
        per_aspect[aspect, status] += 1
        per_go[aspect, status, go_id] += 1
        unique_go[aspect, status].add(go_id)
        encountered_go.add(go_id)
        per_length[bin_label, aspect, status] += 1
        per_taxon[info["taxid"], aspect, status] += 1
        if status == "EVAL":
            per_length_go[bin_label, aspect].add(go_id)
            per_taxon_go[info["taxid"], aspect].add(go_id)
        info["counts"][aspect, status] += 1
        if weights is not None:
            group = (aspect, status)
            if go_id not in weights:
                ia_stats[group][1] += 1
                if status == "EVAL":
                    length_ia[bin_label][1] += 1
                continue
            weight = weights[go_id]
            weight_bin = ia_bin(weight)
            ia_hist[aspect, status, weight_bin] += 1
            ia_unique_terms[aspect, status, weight_bin].add(go_id)
            ia_stats[group][0] += 1
            ia_stats[group][2] += weight
            if status == "EVAL":
                info["eval_ia_sum"] += weight
                length_ia[bin_label][0] += 1
                length_ia[bin_label][2] += weight

    for (accession, aspect), expected in declared_counts.items():
        for status, count in expected.items():
            observed = proteins[accession]["counts"][aspect, status]
            if observed != count:
                raise ValueError(
                    f"target_aspects count mismatch for "
                    f"{accession}/{aspect}/{status}: {count} != {observed}"
                )
        if proteins[accession]["counts"][aspect, "EVAL"] == 0:
            raise ValueError(f"Eligible aspect has no EVAL: {accession}/{aspect}")

    reasons = Counter()
    reason_by_aspect = Counter()
    reason_ia = defaultdict(lambda: [0, 0, 0.0])
    for row in tsv_rows(root / "term_audit.tsv"):
        accession = row["query_id"]
        aspect = row["aspect"]
        if (accession, aspect) not in eligible:
            continue
        status = row["status"]
        reason = row["reason"]
        reasons[status, reason] += 1
        reason_by_aspect[aspect, status, reason] += 1
        info = proteins[accession]
        if status == "EVAL" and reason in {"NOVEL", "CONFIRMATION"}:
            info["counts"][aspect, reason] += 1
            bin_label = length_bin(info["length"], args.length_edges)
            per_length_reason[bin_label, aspect, reason] += 1
            per_taxon_reason[info["taxid"], aspect, reason] += 1
        if weights is not None and status == "EVAL":
            group = (aspect, reason)
            if row["go_id"] in weights:
                weight = weights[row["go_id"]]
                reason_ia[group][0] += 1
                reason_ia[group][2] += weight
                weight_bin = ia_bin(weight)
                ia_hist[aspect, reason, weight_bin] += 1
                ia_unique_terms[aspect, reason, weight_bin].add(row["go_id"])
            else:
                reason_ia[group][1] += 1

    all_aspects = Counter()
    taxon_proteins = defaultdict(set)
    taxon_eligible_aspects = Counter()
    length_proteins = defaultdict(set)
    length_aspects = Counter()
    protein_lengths = []
    features = []
    for accession, info in sorted(proteins.items()):
        length = info["length"]
        label = length_bin(length, args.length_edges)
        protein_lengths.append(length)
        taxon_proteins[info["taxid"]].add(accession)
        length_proteins[label].add(accession)
        for aspect in info["aspects"]:
            all_aspects[aspect] += 1
            taxon_eligible_aspects[info["taxid"], aspect] += 1
            length_aspects[label, aspect] += 1
        counts = info["counts"]
        features.append(
            {
                "query_id": accession,
                "taxid": info["taxid"],
                "length_aa": length,
                "length_bin": label,
                "eligible_aspects": ",".join(sorted(info["aspects"])),
                **{
                    f"{aspect}_{status.lower()}": counts[aspect, status]
                    for aspect in ASPECTS
                    for status in STATUSES
                },
                **{
                    f"{aspect}_{reason.lower()}": counts[aspect, reason]
                    for aspect in ASPECTS
                    for reason in ("NOVEL", "CONFIRMATION")
                },
                "eval_ia_sum": (
                    round(info["eval_ia_sum"], 6) if weights is not None else "NA"
                ),
            }
        )

    expected_bins = [
        *(
            f"{left}-{right - 1}"
            for left, right in zip(
                args.length_edges,
                args.length_edges[1:],
                strict=False,
            )
        ),
        f"{args.length_edges[-1]}+",
    ]
    aspect_rows = [
        {
            "aspect": aspect,
            "eligible_proteins": all_aspects[aspect],
            **{
                f"{status.lower()}_associations": per_aspect[aspect, status]
                for status in STATUSES
            },
            **{
                f"{status.lower()}_unique_go": len(unique_go[aspect, status])
                for status in STATUSES
            },
        }
        for aspect in ASPECTS
    ]
    length_rows = [
        {
            "length_bin": label,
            "proteins": len(length_proteins[label]),
            "eval_ia_mean": (
                round(length_ia[label][2] / length_ia[label][0], 6)
                if weights and length_ia[label][0]
                else "NA"
            ),
            "eval_ia_missing": (length_ia[label][1] if weights else "NA"),
            **{
                f"{aspect}_proteins": length_aspects[label, aspect]
                for aspect in ASPECTS
            },
            **{
                f"{aspect}_{status.lower()}": per_length[label, aspect, status]
                for aspect in ASPECTS
                for status in STATUSES
            },
            **{
                f"{aspect}_eval_unique_go": len(per_length_go[label, aspect])
                for aspect in ASPECTS
            },
            **{
                f"{aspect}_{reason.lower()}": per_length_reason[label, aspect, reason]
                for aspect in ASPECTS
                for reason in ("NOVEL", "CONFIRMATION")
            },
        }
        for label in expected_bins
    ]
    taxon_rows = [
        {
            "taxid": taxid,
            "scientific_name": taxon_names.get(taxid, ""),
            "proteins": len(accessions),
            **{
                f"{aspect}_proteins": taxon_eligible_aspects[taxid, aspect]
                for aspect in ASPECTS
            },
            **{
                f"{aspect}_{status.lower()}": per_taxon[taxid, aspect, status]
                for aspect in ASPECTS
                for status in STATUSES
            },
            **{
                f"{aspect}_eval_unique_go": len(per_taxon_go[taxid, aspect])
                for aspect in ASPECTS
            },
            **{
                f"{aspect}_{reason.lower()}": per_taxon_reason[taxid, aspect, reason]
                for aspect in ASPECTS
                for reason in ("NOVEL", "CONFIRMATION")
            },
        }
        for taxid, accessions in sorted(
            taxon_proteins.items(), key=lambda pair: (-len(pair[1]), pair[0])
        )
    ]
    go_rows = [
        {
            "aspect": aspect,
            "status": status,
            "go_id": term,
            "proteins": number,
            "ia": weights.get(term, "") if weights else "",
        }
        for (aspect, status, term), number in sorted(
            per_go.items(),
            key=lambda pair: (pair[0][0], pair[0][1], -pair[1], pair[0][2]),
        )
    ]
    negative_rows = []
    negative_path = root / "negative_terms.tsv"
    if negative_path.is_file():
        negative_counts = Counter()
        for item in tsv_rows(negative_path):
            acc = item["query_id"]
            asp = item["aspect"]
            if (acc, asp) not in eligible:
                continue
            negative_counts[asp, item.get("decision", ""), item.get("reason", "")] += 1
        negative_rows = [
            {"aspect": a, "decision": d, "reason": r, "associations": count}
            for (a, d, r), count in sorted(negative_counts.items())
        ]
    reason_rows = [
        {"aspect": aspect, "status": status, "reason": reason, "associations": number}
        for (aspect, status, reason), number in sorted(reason_by_aspect.items())
    ]
    feature_columns = [
        "query_id",
        "taxid",
        "length_aa",
        "length_bin",
        "eligible_aspects",
        *(f"{aspect}_{status.lower()}" for aspect in ASPECTS for status in STATUSES),
        *(
            f"{aspect}_{reason.lower()}"
            for aspect in ASPECTS
            for reason in ("NOVEL", "CONFIRMATION")
        ),
        "eval_ia_sum",
    ]
    write_tsv(args.output / "protein_features.tsv", feature_columns, features)
    write_tsv(args.output / "aspects.tsv", list(aspect_rows[0]), aspect_rows)
    write_tsv(args.output / "length_bins.tsv", list(length_rows[0]), length_rows)
    write_tsv(args.output / "taxa.tsv", list(taxon_rows[0]), taxon_rows)
    write_tsv(args.output / "go_frequency.tsv", list(go_rows[0]), go_rows)
    write_tsv(args.output / "reasons.tsv", list(reason_rows[0]), reason_rows)
    write_tsv(
        args.output / "negative_observations.tsv",
        ["aspect", "decision", "reason", "associations"],
        negative_rows,
    )

    if weights is not None:
        ia_rows = [
            {
                "aspect": aspect,
                "category": category,
                "ia_bin": bin_label,
                "associations": count,
                "unique_go": len(ia_unique_terms[aspect, category, bin_label]),
            }
            for (aspect, category, bin_label), count in sorted(ia_hist.items())
        ]
        ia_category_rows = [
            {
                "aspect": aspect,
                "category": category,
                "with_ia": entry[0],
                "without_ia": entry[1],
                "mean_ia": round(entry[2] / entry[0], 6) if entry[0] else "NA",
                "sum_ia": round(entry[2], 6),
            }
            for (aspect, category), entry in sorted(
                {
                    **ia_stats,
                    **{k: v for k, v in reason_ia.items() if k not in ia_stats},
                }.items()
            )
        ]
        write_tsv(
            args.output / "ia_bins.tsv",
            ["aspect", "category", "ia_bin", "associations", "unique_go"],
            ia_rows,
        )
        write_tsv(
            args.output / "ia_categories.tsv",
            ["aspect", "category", "with_ia", "without_ia", "mean_ia", "sum_ia"],
            ia_category_rows,
        )

    manifest = json.loads((root / "manifest.json").read_text())
    protein_lengths.sort()
    eval_per_protein = sorted(
        sum(info["counts"][aspect, "EVAL"] for aspect in ASPECTS)
        for info in proteins.values()
    )
    summary = {
        "schema_version": "probe-benchmark-descriptive-profile-v1",
        "source_schema": manifest.get("schema_version", "unknown"),
        "policy_sha256": manifest.get("policy_sha256"),
        "source_benchmark": str(root.resolve()),
        "source_manifest_sha256": sha256_file(root / "manifest.json"),
        "stage01_target_count": identity["stage01_targets"],
        "identical_target_aliases": identity["identical_aliases"],
        "eligible_distinct_proteins": len(proteins),
        "eligible_target_aspects": len(eligible),
        "distinct_taxids": len(taxon_proteins),
        "eval_terms_per_protein": {
            "mean": round(statistics.mean(eval_per_protein), 6),
            "median": statistics.median(eval_per_protein),
            "p10": percentile(eval_per_protein, 0.1),
            "p90": percentile(eval_per_protein, 0.9),
            "p99": percentile(eval_per_protein, 0.99),
            "max": max(eval_per_protein),
        },
        "associations": dict(total),
        "unique_go_by_aspect_status": {
            f"{aspect}_{status}": len(unique_go[aspect, status])
            for aspect in ASPECTS
            for status in STATUSES
        },
        "total_distinct_go_across_statuses": len(encountered_go),
        "eval_reasons": {
            reason: reasons["EVAL", reason] for reason in ("NOVEL", "CONFIRMATION")
        },
        "protein_lengths_aa": {
            "at_most_1022": sum(x <= 1022 for x in protein_lengths),
            "over_1022": sum(x > 1022 for x in protein_lengths),
            "min": min(protein_lengths) if protein_lengths else None,
            "median": statistics.median(protein_lengths) if protein_lengths else None,
            "p10": percentile(protein_lengths, 0.1),
            "p90": percentile(protein_lengths, 0.9),
            "max": max(protein_lengths) if protein_lengths else None,
        },
        "filters": {
            "min_length_aa": args.min_length,
            "max_length_aa": args.max_length,
            "taxids": sorted(selected_taxa) if selected_taxa else [],
            "length_bin_edges": args.length_edges,
        },
        "information_accretion": {
            "status": "supplied_frozen_weights" if weights else "not_available",
            "ia_file": str(args.ia.resolve()) if weights else None,
            "ia_sha256": sha256_file(args.ia) if weights else None,
            "defined_weights": len(weights) if weights else 0,
            "missing_association_weights": sum(item[1] for item in ia_stats.values())
            if weights
            else None,
            "warning": (
                "Weights must use a declared START reference corpus, "
                "GO graph and propagated annotation policy."
                if weights
                else "IA cannot be inferred from EVAL frequency."
            ),
        },
        "interpretation": (
            "GO counts are per protein-GO pairs; unique GO counts "
            "deduplicate terms across proteins. Filters do not change "
            "the benchmark EVAL/SKIP/NEUTRAL policy."
        ),
    }
    unfiltered = (
        args.min_length is None and args.max_length is None and selected_taxa is None
    )
    if unfiltered:
        canonical_counts = manifest.get("counts", {})
        for status in STATUSES:
            published = canonical_counts.get(f"benchmark_{status}")
            if published is not None and published != total[status]:
                raise ValueError(
                    f"Published {status} count {published} != {total[status]}"
                )
        expected = canonical_counts.get("eligible_target_aspects")
        if expected is not None and expected != len(eligible):
            raise ValueError("Published target/aspect count mismatch")
    if reasons["EVAL", "NOVEL"] + reasons["EVAL", "CONFIRMATION"] != total["EVAL"]:
        raise ValueError("EVAL not partitioned into NOVEL/CONFIRMATION")
    if sum(n for (s, _), n in reasons.items() if s == "NEUTRAL") != total["NEUTRAL"]:
        raise ValueError("NEUTRAL audit count differs from benchmark")
    if sum(n for (s, _), n in reasons.items() if s == "SKIP") != total["SKIP"]:
        raise ValueError("SKIP audit count differs from benchmark")
    (args.output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    md = [
        "# ProBE — Descriptive benchmark profile",
        "",
        f"- Benchmark: `{root}`",
        f"- Eligible distinct proteins: **{len(proteins):,}**",
        f"- Eligible protein/aspect pairs: **{len(eligible):,}**",
        f"- Distinct NCBI TaxIDs: **{len(taxon_proteins):,}**",
        f"- Total distinct GO IDs across statuses: **{len(encountered_go):,}**",
        f"- Mean EVAL terms/protein: **{statistics.mean(eval_per_protein):.2f}**",
        f"- Median EVAL terms/protein: **{statistics.median(eval_per_protein):.1f}**",
        "",
        "## GO associations and unique terms",
        "",
        "| Aspect | EVAL pairs | EVAL GO unique | SKIP pairs | "
        "SKIP GO unique | NEUTRAL pairs | NEUTRAL GO unique |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in aspect_rows:
        md.append(
            f"| {row['aspect']} | {row['eval_associations']:,} | "
            f"{row['eval_unique_go']:,} | {row['skip_associations']:,} | "
            f"{row['skip_unique_go']:,} | "
            f"{row['neutral_associations']:,} | "
            f"{row['neutral_unique_go']:,} |"
        )
    md.extend(
        [
            "",
            "## Target length bins",
            "",
            "| Length (aa) | Proteins | MF | BP | CC |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for row in length_rows:
        md.append(
            f"| {row['length_bin']} | {row['proteins']:,} | "
            f"{row['F_proteins']:,} | {row['P_proteins']:,} | "
            f"{row['C_proteins']:,} |"
        )
    md.extend(
        [
            "",
            "## Top taxa",
            "",
            "| TaxID | Scientific name | Proteins |",
            "|---|---|---:|",
        ]
    )
    for row in taxon_rows[:15]:
        md.append(
            f"| {row['taxid']} | {row['scientific_name']} | {row['proteins']:,} |"
        )
    md.extend(
        [
            "",
            "## Information accretion",
            "",
            "IA weights: "
            + (
                "provided externally; consult ia_bins.tsv and ia_categories.tsv."
                if weights
                else "NOT available; no substitute was invented."
            ),
            "",
            "Counts of GO associations are redundant over proteins; unique GO "
            "counts are deduplicated within each aspect and status.",
            "The protein-length filter is descriptive only: it does not change "
            "M12 truth or scoring eligibility for other proteins.",
            "",
        ]
    )
    (args.output / "REPORT.md").write_text("\n".join(md), encoding="utf-8")
    print(
        json.dumps(
            {
                "proteins": len(proteins),
                "protein_aspects": len(eligible),
                "taxa": len(taxon_proteins),
                "associations": dict(total),
                "distinct_go": len(encountered_go),
                "ia_available": weights is not None,
                "output": str(args.output),
            },
            sort_keys=True,
        )
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--targets", type=Path, required=True)
    parser.add_argument("--stage01", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ia", type=Path)
    parser.add_argument("--taxon-names", type=Path)
    parser.add_argument("--min-length", type=int)
    parser.add_argument("--max-length", type=int)
    parser.add_argument("--taxids", help="Comma-separated NCBI TaxIDs")
    parser.add_argument(
        "--length-edges",
        default="0,300,600,1023,1500,3000",
        help="Comma-separated lower boundaries, final bin open ended",
    )
    args = parser.parse_args()
    args.length_edges = parse_edges(args.length_edges)
    if args.min_length is not None and args.min_length < 1:
        parser.error("--min-length must be >=1")
    if args.max_length is not None and args.max_length < 1:
        parser.error("--max-length must be >=1")
    if (
        args.min_length is not None
        and args.max_length is not None
        and args.min_length > args.max_length
    ):
        parser.error("Length filter has min > max")
    final_output = args.output
    if not final_output.parent.is_dir():
        parser.error("Output parent directory must already exist")
    if final_output.exists() or final_output.is_symlink():
        parser.error("Output already exists; published diagnostics are immutable")
    temp_name = f".{final_output.name}.stage-{uuid.uuid4().hex}"
    args.output = final_output.parent / temp_name
    try:
        probe(args)
        inputs = {
            "schema_version": "probe-diagnostics-publication-v1",
            "benchmark_manifest_sha256": sha256_file(args.benchmark / "manifest.json"),
            "targets_sha256": sha256_file(args.targets),
            "stage01_sha256": sha256_file(args.stage01),
            "ia_sha256": sha256_file(args.ia) if args.ia else None,
            "length_edges": list(args.length_edges),
            "min_length": args.min_length,
            "max_length": args.max_length,
            "taxids": args.taxids,
            "outputs": {},
        }
        for path in sorted(args.output.iterdir()):
            if path.is_file():
                inputs["outputs"][path.name] = {
                    "sha256": sha256_file(path),
                    "bytes": path.stat().st_size,
                }
        (args.output / "publication_manifest.json").write_text(
            json.dumps(inputs, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if final_output.exists() or final_output.is_symlink():
            raise FileExistsError("Diagnostics destination appeared during run")
        os.rename(args.output, final_output)
        print(f"DIAGNOSTICS_PUBLISHED={final_output}")
    except BaseException:
        if args.output.is_dir():
            shutil.rmtree(args.output)
        raise


if __name__ == "__main__":
    main()
