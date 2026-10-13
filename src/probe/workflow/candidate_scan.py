"""Shared preflight and empty-output helpers for candidate scanners."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

PAIR_COLUMNS = (
    "record_number",
    "accession",
    "entry",
    "rid",
    "src",
    "rec",
    "raw_ox",
    "resolved_taxid",
    "species_taxid",
    "frag",
    "pe",
    "sv",
    "flags",
    "sec",
    "gene",
    "target_id",
    "target_bucket",
    "target_length",
    "candidate_length",
    "length_ratio",
    "candidate_over_target_length",
    "target_distinct_kmers",
    "shared_distinct_kmers",
    "target_shared_fraction",
    "exact_sequence",
    "shared_unique_positional_kmers",
    "target_seed_span_fraction",
    "candidate_seed_span_fraction",
    "target_seed_residue_coverage",
    "candidate_seed_residue_coverage",
    "lis_count",
    "lis_fraction",
    "diagonal_min",
    "diagonal_max",
    "diagonal_range",
)

UNSEARCHABLE_COLUMNS = (
    "target_id",
    "length",
    "reason",
)

SUMMARY_ZERO_METRICS = (
    "fasta_records_total",
    "records_missing_ox",
    "records_malformed_ox",
    "records_ox_not_in_taxonomy_map",
    "records_no_primary_bucket",
    "records_fragment_skipped",
    "records_primary_eligible",
    "records_primary_len_lt_k",
    "records_with_emitted_pair",
    "pairs_emitted",
    "exact_sequence_pairs",
    "pairs_positional_seed_zero",
    "pairs_positional_seed_ge1",
    "pairs_positional_seed_ge5",
    "pairs_positional_seed_ge10",
    "pairs_bidir_span_ge_0.25",
    "pairs_bidir_span_ge_0.50",
    "pairs_bidir_span_ge_0.75",
    "pairs_bidir_span_ge_0.90",
    "pairs_bidir_span_ge_0.95",
    "pairs_bidir_seed_cov_ge_0.25",
    "pairs_bidir_seed_cov_ge_0.50",
    "pairs_bidir_seed_cov_ge_0.75",
    "pairs_bidir_seed_cov_ge_0.90",
    "pairs_lis_fraction_ge_0.50",
    "pairs_lis_fraction_ge_0.75",
    "pairs_lis_fraction_ge_0.90",
    "pairs_lis_fraction_ge_0.95",
    "pairs_length_ratio_ge_0.50",
    "pairs_length_ratio_ge_0.75",
    "pairs_length_ratio_ge_0.90",
    "pairs_length_ratio_ge_0.95",
    "max_pairs_for_one_record",
)


@dataclass(frozen=True)
class CandidateScanPreflight:
    target_count: int
    taxonomy_count: int
    eligible_target_count: int
    unresolved_target_count: int
    empty_primary_bucket_count: int


@dataclass(frozen=True)
class CandidateScanSummary:
    targets_loaded: int
    pairs_emitted: int


def _load_unique_rows(
    path: Path,
    *,
    required_columns: set[str],
    label: str,
) -> dict[str, dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")

        if reader.fieldnames is None:
            raise ValueError(f"{label}: missing TSV header")

        missing = required_columns.difference(reader.fieldnames)
        if missing:
            raise ValueError(f"{label}: missing columns {sorted(missing)}")

        rows: dict[str, dict[str, str]] = {}

        for line_number, row in enumerate(reader, start=2):
            target_id = row["target_id"]

            if not target_id:
                raise ValueError(f"{label}: empty target_id at line {line_number}")

            if target_id in rows:
                raise ValueError(f"{label}: duplicate target_id {target_id}")

            rows[target_id] = row

    return rows


def preflight_candidate_scan(
    targets_path: Path,
    target_taxonomy_path: Path,
    *,
    taxonomy_policy: str,
) -> CandidateScanPreflight:
    """Validate scanner inputs and classify target taxonomy eligibility."""

    if taxonomy_policy not in {"rank_aware", "strict_taxon", "species_wide"}:
        raise ValueError(f"unknown taxonomy_policy {taxonomy_policy!r}")

    targets = _load_unique_rows(
        targets_path,
        required_columns={"target_id", "normalized_sequence"},
        label="candidate scan targets",
    )

    taxonomy = _load_unique_rows(
        target_taxonomy_path,
        required_columns={
            "target_id",
            "resolved_taxid",
            "species_taxid",
            "primary_bucket",
        },
        label="candidate scan target taxonomy",
    )

    target_ids = set(targets)
    taxonomy_ids = set(taxonomy)

    missing_taxonomy = sorted(target_ids - taxonomy_ids)
    if missing_taxonomy:
        raise ValueError(
            "candidate scan target taxonomy is missing target_ids "
            f"{missing_taxonomy[:10]}"
        )

    extra_taxonomy = sorted(taxonomy_ids - target_ids)
    if extra_taxonomy:
        raise ValueError(
            "candidate scan target taxonomy has unexpected target_ids "
            f"{extra_taxonomy[:10]}"
        )

    eligible = 0
    unresolved = 0
    empty_primary_bucket = 0

    for target_id in targets:
        row = taxonomy[target_id]

        if not row["resolved_taxid"] or not row["species_taxid"]:
            unresolved += 1
            continue

        if taxonomy_policy == "rank_aware" and not row["primary_bucket"]:
            empty_primary_bucket += 1
            continue

        eligible += 1

    return CandidateScanPreflight(
        target_count=len(targets),
        taxonomy_count=len(taxonomy),
        eligible_target_count=eligible,
        unresolved_target_count=unresolved,
        empty_primary_bucket_count=empty_primary_bucket,
    )


def read_candidate_scan_summary(path: Path) -> CandidateScanSummary:
    """Read scanner counts needed for workflow outcome classification."""

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t")

        try:
            header = next(reader)
        except StopIteration as error:
            raise ValueError(f"{path}: empty candidate scan summary") from error

        if header != ["metric", "value"]:
            raise ValueError(
                f"{path}: expected summary header ['metric', 'value'], got {header}"
            )

        metrics: dict[str, str] = {}

        for line_number, row in enumerate(reader, start=2):
            if len(row) != 2:
                raise ValueError(f"{path}: malformed summary row at line {line_number}")

            key, value = row

            if not key:
                raise ValueError(f"{path}: empty summary metric at line {line_number}")

            if key in metrics:
                raise ValueError(f"{path}: duplicate summary metric {key}")

            metrics[key] = value

    counts: dict[str, int] = {}

    for key in ("targets_loaded", "pairs_emitted"):
        if key not in metrics:
            raise ValueError(f"{path}: missing summary metric {key}")

        try:
            value = int(metrics[key])
        except ValueError as error:
            raise ValueError(
                f"{path}: summary metric {key} is not an integer"
            ) from error

        if value < 0:
            raise ValueError(f"{path}: summary metric {key} must be nonnegative")

        counts[key] = value

    return CandidateScanSummary(
        targets_loaded=counts["targets_loaded"],
        pairs_emitted=counts["pairs_emitted"],
    )


def write_empty_candidate_scan_outputs(
    output_prefix: Path,
    *,
    snapshot: str,
    policy: dict[str, object],
) -> None:
    """Write scanner-compatible empty outputs without invoking the C++ binary."""

    pairs_path = Path(f"{output_prefix}.pairs.tsv")
    unsearchable_path = Path(f"{output_prefix}.unsearchable_targets.tsv")
    summary_path = Path(f"{output_prefix}.summary.tsv")

    pairs_path.write_text(
        "\t".join(PAIR_COLUMNS) + "\n",
        encoding="utf-8",
    )

    unsearchable_path.write_text(
        "\t".join(UNSEARCHABLE_COLUMNS) + "\n",
        encoding="utf-8",
    )

    rows: list[tuple[str, object]] = [
        ("snapshot", snapshot),
        ("k", policy["k"]),
        ("max_required_shared", policy["max_required_shared"]),
        (
            "min_target_kmer_fraction",
            policy["min_target_kmer_fraction"],
        ),
        ("fragment_policy", policy["fragment_policy"]),
        ("taxonomy_policy", policy["taxonomy_policy"]),
        ("targets_loaded", 0),
        ("targets_unsearchable_len_lt_k", 0),
    ]

    rows.extend((metric, 0) for metric in SUMMARY_ZERO_METRICS)

    with summary_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("metric\tvalue\n")
        for key, value in rows:
            handle.write(f"{key}\t{value}\n")
