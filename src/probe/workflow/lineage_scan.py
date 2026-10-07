"""Failover helpers for the Stage 04D2 one-hop lineage scanner."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

INDEX_REQUIRED_COLUMNS = {
    "release",
    "target_id",
    "primary_bucket",
    "key_type",
    "key_value",
    "key_target_multiplicity",
    "key_collision",
    "from_target_accession",
    "from_start_anchor",
    "from_end_anchor",
    "source_roles",
}

EVIDENCE_COLUMNS = (
    "release",
    "record_number",
    "target_id",
    "target_primary_bucket",
    "key_type",
    "key_value",
    "record_match_role",
    "key_target_multiplicity",
    "key_collision",
    "from_target_accession",
    "from_start_anchor",
    "from_end_anchor",
    "source_roles",
    "candidate_primary_accession",
    "entry",
    "rid",
    "raw_ox",
    "resolved_taxid",
    "species_taxid",
    "frag",
    "sequence_length",
    "pe",
    "sv",
    "sec",
    "gene",
)

ZERO_SUMMARY_METRICS = (
    "full_ox_taxonomy_count",
    "full_v2_record_count",
    "records_missing_ox_taxonomy",
    "records_without_candidate_bucket",
    "records_with_lineage_match",
    "unique_target_record_pairs",
    "targets_with_lineage_match",
    "evidence_rows",
    "collision_evidence_rows",
    "fragment_evidence_rows",
    "max_evidence_rows_for_one_record",
    "max_targets_for_one_record",
)


@dataclass(frozen=True)
class LineageScanPreflight:
    posting_count: int
    total_posting_count: int


@dataclass(frozen=True)
class LineageScanSummary:
    release: str
    postings_loaded: int
    evidence_rows: int


def preflight_lineage_scan_index(
    path: Path,
    *,
    release: str,
    allowed_releases: set[str],
) -> LineageScanPreflight:
    """Validate the frozen scan index and count postings for one release."""

    if release not in allowed_releases:
        raise ValueError(
            f"lineage scan release {release!r} is absent from allowed releases"
        )

    posting_count = 0
    total_posting_count = 0

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")

        if reader.fieldnames is None:
            raise ValueError(f"{path}: missing TSV header")

        missing = INDEX_REQUIRED_COLUMNS.difference(reader.fieldnames)
        if missing:
            raise ValueError(f"{path}: missing columns {sorted(missing)}")

        for line_number, row in enumerate(reader, start=2):
            row_release = row["release"]

            if row_release not in allowed_releases:
                raise ValueError(
                    f"{path}: unexpected release {row_release!r} at line {line_number}"
                )

            for field in (
                "target_id",
                "primary_bucket",
                "key_type",
                "key_value",
            ):
                if not row[field]:
                    raise ValueError(f"{path}: empty {field} at line {line_number}")

            bucket = row["primary_bucket"]
            if (
                not (bucket.startswith("S:") or bucket.startswith("T:"))
                or len(bucket) <= 2
            ):
                raise ValueError(
                    f"{path}: invalid primary_bucket {bucket!r} at line {line_number}"
                )

            if row["key_type"] not in {
                "ACCESSION",
                "ORDERED_LOCUS",
                "ORF",
            }:
                raise ValueError(
                    f"{path}: invalid key_type {row['key_type']!r} "
                    f"at line {line_number}"
                )

            try:
                multiplicity = int(row["key_target_multiplicity"])
            except ValueError as error:
                raise ValueError(
                    f"{path}: non-integer key_target_multiplicity at line {line_number}"
                ) from error

            if multiplicity < 1:
                raise ValueError(
                    f"{path}: key_target_multiplicity must be >= 1 "
                    f"at line {line_number}"
                )

            for field in (
                "key_collision",
                "from_target_accession",
                "from_start_anchor",
                "from_end_anchor",
            ):
                if row[field] not in {"0", "1"}:
                    raise ValueError(
                        f"{path}: invalid boolean {field}={row[field]!r} "
                        f"at line {line_number}"
                    )

            total_posting_count += 1
            if row_release == release:
                posting_count += 1

    return LineageScanPreflight(
        posting_count=posting_count,
        total_posting_count=total_posting_count,
    )


def _read_summary_metrics(path: Path) -> dict[str, str]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t")

        try:
            header = next(reader)
        except StopIteration as error:
            raise ValueError(f"{path}: empty summary") from error

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

    return metrics


def read_lineage_scan_summary(
    path: Path,
    *,
    expected_release: str,
) -> LineageScanSummary:
    """Read the scanner metrics needed for workflow outcome classification."""

    metrics = _read_summary_metrics(path)

    release = metrics.get("release")
    if release != expected_release:
        raise ValueError(f"{path}: expected release {expected_release}, got {release}")

    counts: dict[str, int] = {}

    for key in ("scan_index_postings_loaded", "evidence_rows"):
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

    return LineageScanSummary(
        release=release,
        postings_loaded=counts["scan_index_postings_loaded"],
        evidence_rows=counts["evidence_rows"],
    )


def write_empty_lineage_scan_outputs(
    output_path: Path,
    summary_path: Path,
    *,
    release: str,
) -> None:
    """Write scanner-compatible empty outputs without invoking the C++ binary."""

    output_path.write_text(
        "\t".join(EVIDENCE_COLUMNS) + "\n",
        encoding="utf-8",
    )

    rows: list[tuple[str, object]] = [
        ("release", release),
        ("scan_index_postings_loaded", 0),
    ]
    rows.extend((metric, 0) for metric in ZERO_SUMMARY_METRICS)

    with summary_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("metric\tvalue\n")
        for key, value in rows:
            handle.write(f"{key}\t{value}\n")
