"""Prepare strict canonical external targets for later exact matching."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import sqlite3
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from probe.source import Source
from probe.target_mapping import IUPAC_PROTEIN_SYMBOLS

CANONICAL_SEQUENCE_POLICY_VERSION = "protein-sequence-v1"
TARGETS_FILENAME = "01_targets.tsv"
REJECTED_FILENAME = "01_rejected.tsv"
SUMMARY_FILENAME = "01_summary.json"
_METADATA_HEADER = ("target_id", "ncbi_taxid", "uniprot_accession")
_INVALID_SEQUENCE_REASON = (
    "submitted sequence cannot be verified by strict exact UniProtKB identity "
    "matching in the supplied form"
)


@dataclass(frozen=True, slots=True)
class TargetPreparationResult:
    targets_path: Path
    rejected_path: Path
    summary_path: Path
    accepted_count: int
    rejected_count: int


@dataclass(frozen=True, slots=True)
class PreparedTarget:
    """One accepted canonical target retained for downstream exact comparison."""

    target_id: str
    raw_taxid: str
    optional_accession: str
    normalized_sequence: str
    sequence_length: int
    sequence_sha256: str
    normalization_policy_version: str


@dataclass(frozen=True, slots=True)
class _FastaTarget:
    target_id: str
    sequence: str
    line: int
    sequence_line: int | None
    status: str
    reason: str


def _header_target_id(header: str) -> tuple[str, str, str]:
    """Return the audit target ID and any strict-header rejection details."""

    target_id = header.split(maxsplit=1)[0] if header.split() else ""
    if not header:
        return target_id, "INVALID_FASTA_HEADER", "FASTA header has no target_id"
    if header != target_id or any(character.isspace() for character in header):
        return (
            target_id,
            "INVALID_FASTA_HEADER",
            "FASTA header must be exactly >target_id with no whitespace or metadata",
        )
    return target_id, "VALID", ""


def _iter_targets(source: Source) -> Iterator[_FastaTarget]:
    """Read strict two-line canonical FASTA records without repairing layout."""

    header: str | None = None
    header_line = 0
    sequence_lines: list[tuple[str, int]] = []

    def finish() -> _FastaTarget | None:
        if header is None:
            return None
        target_id, status, reason = _header_target_id(header)
        if len(sequence_lines) != 1:
            return _FastaTarget(
                target_id,
                "".join(line for line, _ in sequence_lines),
                header_line,
                None,
                "INVALID_FASTA_LAYOUT",
                "canonical FASTA record must contain exactly one physical "
                "sequence line",
            )
        sequence, sequence_line = sequence_lines[0]
        return _FastaTarget(
            target_id, sequence, header_line, sequence_line, status, reason
        )

    with source.open_text() as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.rstrip("\r\n")
            if line.startswith(">"):
                if target := finish():
                    yield target
                header = line[1:]
                header_line = line_number
                sequence_lines = []
            elif header is None:
                raise ValueError(
                    "FASTA sequence precedes first header at "
                    f"{source.name}:{line_number}"
                )
            else:
                sequence_lines.append((line, line_number))
    if target := finish():
        yield target


def _invalid_symbols(sequence: str) -> tuple[str, str]:
    invalid = [
        (position, symbol)
        for position, symbol in enumerate(sequence, start=1)
        if symbol not in IUPAC_PROTEIN_SYMBOLS
    ]
    symbols = ",".join(sorted({symbol for _, symbol in invalid}))
    positions = ",".join(f"{symbol}@{position}" for position, symbol in invalid)
    return symbols, positions


def _validate_sequence(
    target: _FastaTarget,
) -> tuple[str | None, int | None, str | None, str, str, str, str]:
    """Validate literal v1 sequence input; never repair submitted biology."""

    if target.status != "VALID":
        return None, None, None, target.status, target.reason, "", ""
    if not target.sequence:
        return (
            None,
            None,
            None,
            "INVALID_SEQUENCE",
            "canonical FASTA sequence line is empty",
            "",
            "",
        )
    symbols, positions = _invalid_symbols(target.sequence)
    if symbols:
        return (
            None,
            None,
            None,
            "INVALID_SEQUENCE_SYMBOLS",
            _INVALID_SEQUENCE_REASON,
            symbols,
            positions,
        )
    digest = hashlib.sha256(target.sequence.encode("ascii")).hexdigest()
    return (
        target.sequence,
        len(target.sequence),
        digest,
        "VALID",
        "",
        "",
        "",
    )


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE metadata_rows (
            row_number INTEGER PRIMARY KEY,
            target_id TEXT NOT NULL,
            raw_taxid TEXT NOT NULL,
            optional_accession TEXT NOT NULL,
            status TEXT NOT NULL,
            reason TEXT NOT NULL,
            source_line INTEGER NOT NULL
        );
        CREATE INDEX metadata_rows_target_id_idx ON metadata_rows (target_id);
        CREATE TABLE fasta_rows (
            record_number INTEGER PRIMARY KEY,
            target_id TEXT NOT NULL,
            raw_taxid TEXT,
            optional_accession TEXT,
            normalized_sequence TEXT,
            sequence_length INTEGER,
            sequence_sha256 TEXT,
            status TEXT NOT NULL,
            reason TEXT NOT NULL,
            offending_symbols TEXT NOT NULL,
            offending_positions TEXT NOT NULL,
            source_line INTEGER NOT NULL
        );
        CREATE INDEX fasta_rows_target_id_idx ON fasta_rows (target_id);
        """
    )


def _load_metadata(connection: sqlite3.Connection, source: Source) -> None:
    with source.open_text() as handle:
        reader = csv.reader(handle, delimiter="\t")
        try:
            header = next(reader)
        except StopIteration as error:
            raise ValueError("target metadata TSV is empty") from error
        if tuple(header) != _METADATA_HEADER:
            raise ValueError(
                "target metadata TSV must have exactly this header and column order: "
                "target_id, ncbi_taxid, uniprot_accession"
            )
        for row_number, row in enumerate(reader, start=2):
            if len(row) != len(_METADATA_HEADER):
                raise ValueError(
                    f"metadata row at {source.name}:{row_number} has an "
                    "unsupported schema"
                )
            target_id, raw_taxid, optional_accession = row
            if not target_id:
                status, reason = (
                    "MISSING_METADATA_TARGET_ID",
                    "metadata row has no target_id",
                )
            elif any(character.isspace() for character in target_id):
                status, reason = (
                    "INVALID_METADATA_TARGET_ID",
                    "metadata target_id must contain no whitespace",
                )
            elif not raw_taxid.strip():
                status, reason = "MISSING_TAXID", "metadata row has no ncbi_taxid"
            else:
                status, reason = "VALID", ""
            connection.execute(
                """
                INSERT INTO metadata_rows (
                    row_number, target_id, raw_taxid, optional_accession, status,
                    reason, source_line
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row_number,
                    target_id,
                    raw_taxid,
                    optional_accession,
                    status,
                    reason,
                    row_number,
                ),
            )
    connection.execute(
        """
        UPDATE metadata_rows
        SET status = 'DUPLICATE_METADATA_TARGET_ID',
            reason = 'target_id occurs more than once in metadata'
        WHERE target_id != '' AND target_id IN (
            SELECT target_id FROM metadata_rows
            GROUP BY target_id HAVING COUNT(*) > 1
        )
        """
    )


def _load_fasta_rows(connection: sqlite3.Connection, source: Source) -> None:
    for record_number, target in enumerate(_iter_targets(source), start=1):
        (
            normalized_sequence,
            sequence_length,
            sequence_sha256,
            status,
            reason,
            offending_symbols,
            offending_positions,
        ) = _validate_sequence(target)
        metadata = connection.execute(
            """
            SELECT raw_taxid, optional_accession, status, reason
            FROM metadata_rows WHERE target_id = ? ORDER BY row_number
            """,
            (target.target_id,),
        ).fetchall()
        raw_taxid: str | None = None
        optional_accession: str | None = None
        if len(metadata) == 1:
            raw_taxid, optional_accession, metadata_status, metadata_reason = metadata[
                0
            ]
            if status == "VALID" and metadata_status != "VALID":
                status, reason = metadata_status, metadata_reason
        elif not metadata and status == "VALID":
            status, reason = "MISSING_METADATA", "no metadata row for target_id"
        elif len(metadata) > 1 and status == "VALID":
            status, reason = (
                "DUPLICATE_METADATA_TARGET_ID",
                "target_id occurs more than once in metadata",
            )
        connection.execute(
            """
            INSERT INTO fasta_rows (
                record_number, target_id, raw_taxid, optional_accession,
                normalized_sequence, sequence_length, sequence_sha256, status,
                reason, offending_symbols, offending_positions, source_line
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_number,
                target.target_id,
                raw_taxid,
                optional_accession,
                normalized_sequence,
                sequence_length,
                sequence_sha256,
                status,
                reason,
                offending_symbols,
                offending_positions,
                target.sequence_line or target.line,
            ),
        )
    connection.execute(
        """
        UPDATE fasta_rows
        SET status = 'DUPLICATE_FASTA_TARGET_ID',
            reason = 'target_id occurs more than once in FASTA'
        WHERE target_id != '' AND target_id IN (
            SELECT target_id FROM fasta_rows
            GROUP BY target_id HAVING COUNT(*) > 1
        )
        """
    )


def _write_outputs(
    connection: sqlite3.Connection,
    output_directory: Path,
    target_source: Source,
    metadata_source: Source,
) -> TargetPreparationResult:
    targets_path = output_directory / TARGETS_FILENAME
    rejected_path = output_directory / REJECTED_FILENAME
    summary_path = output_directory / SUMMARY_FILENAME
    metadata_only_rows = connection.execute(
        """
        SELECT target_id, raw_taxid, source_line, status, reason
        FROM metadata_rows AS metadata
        WHERE NOT EXISTS (
            SELECT 1 FROM fasta_rows WHERE fasta_rows.target_id = metadata.target_id
        )
        ORDER BY row_number
        """
    ).fetchall()
    with tempfile.TemporaryDirectory(dir=output_directory) as temporary_directory:
        temporary = Path(temporary_directory)
        temporary_targets = temporary / TARGETS_FILENAME
        temporary_rejected = temporary / REJECTED_FILENAME
        temporary_summary = temporary / SUMMARY_FILENAME
        with temporary_targets.open("w", encoding="utf-8", newline="\n") as output:
            writer = csv.writer(output, delimiter="\t", lineterminator="\n")
            writer.writerow(
                (
                    "target_id",
                    "raw_taxid",
                    "optional_accession",
                    "normalized_sequence",
                    "sequence_length",
                    "sequence_sha256",
                    "normalization_policy_version",
                )
            )
            for row in connection.execute(
                """
                SELECT target_id, raw_taxid, optional_accession,
                       normalized_sequence, sequence_length, sequence_sha256
                FROM fasta_rows WHERE status = 'VALID' ORDER BY record_number
                """
            ):
                writer.writerow(
                    (
                        row["target_id"],
                        row["raw_taxid"],
                        row["optional_accession"],
                        row["normalized_sequence"],
                        row["sequence_length"],
                        row["sequence_sha256"],
                        CANONICAL_SEQUENCE_POLICY_VERSION,
                    )
                )
            output.flush()
            os.fsync(output.fileno())
        with temporary_rejected.open("w", encoding="utf-8", newline="\n") as output:
            writer = csv.writer(output, delimiter="\t", lineterminator="\n")
            writer.writerow(
                (
                    "target_id",
                    "raw_taxid",
                    "source_line",
                    "status",
                    "severity",
                    "reason",
                    "offending_symbols",
                    "offending_positions",
                )
            )
            for row in connection.execute(
                """
                SELECT target_id, raw_taxid, source_line, status, reason,
                       offending_symbols, offending_positions
                FROM fasta_rows WHERE status != 'VALID' ORDER BY record_number
                """
            ):
                writer.writerow(
                    (
                        row["target_id"],
                        row["raw_taxid"] or "",
                        row["source_line"],
                        row["status"],
                        "ALERT",
                        row["reason"],
                        row["offending_symbols"],
                        row["offending_positions"],
                    )
                )
            for row in metadata_only_rows:
                status = row["status"]
                reason = row["reason"]
                if status == "VALID":
                    status, reason = (
                        "METADATA_ONLY_TARGET",
                        "no FASTA record for target_id",
                    )
                writer.writerow(
                    (
                        row["target_id"],
                        row["raw_taxid"],
                        row["source_line"],
                        status,
                        "ALERT",
                        reason,
                        "",
                        "",
                    )
                )
            output.flush()
            os.fsync(output.fileno())
        accepted_count = connection.execute(
            "SELECT COUNT(*) FROM fasta_rows WHERE status = 'VALID'"
        ).fetchone()[0]
        fasta_rejected_count = connection.execute(
            "SELECT COUNT(*) FROM fasta_rows WHERE status != 'VALID'"
        ).fetchone()[0]
        rejected_count = fasta_rejected_count + len(metadata_only_rows)
        summary = {
            "accepted_count": accepted_count,
            "metadata_only_count": len(metadata_only_rows),
            "metadata_path": metadata_source.name,
            "normalization_policy_version": CANONICAL_SEQUENCE_POLICY_VERSION,
            "rejected_count": rejected_count,
            "target_fasta_path": target_source.name,
            "target_sequence_declared_full_length": True,
            "total_fasta_records": accepted_count + fasta_rejected_count,
        }
        with temporary_summary.open("w", encoding="utf-8", newline="\n") as output:
            json.dump(summary, output, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        for temporary_path, output_path in (
            (temporary_targets, targets_path),
            (temporary_rejected, rejected_path),
            (temporary_summary, summary_path),
        ):
            os.replace(temporary_path, output_path)
    return TargetPreparationResult(
        targets_path=targets_path,
        rejected_path=rejected_path,
        summary_path=summary_path,
        accepted_count=accepted_count,
        rejected_count=rejected_count,
    )


def prepare_targets(
    *,
    target_fasta: str | Path,
    metadata_tsv: str | Path,
    output_directory: str | Path,
) -> TargetPreparationResult:
    """Validate coordinated canonical targets without identity inference."""

    target_source = Source.from_value(target_fasta)
    metadata_source = Source.from_value(metadata_tsv)
    output_directory = Path(output_directory)
    output_directory.mkdir(parents=True, exist_ok=True)
    outputs = {
        output_directory / TARGETS_FILENAME,
        output_directory / REJECTED_FILENAME,
        output_directory / SUMMARY_FILENAME,
    }
    inputs = {target_source.path.resolve(), metadata_source.path.resolve()}
    if any(path.resolve() in inputs for path in outputs):
        raise ValueError("target preparation outputs must not overwrite inputs")
    with tempfile.TemporaryDirectory(dir=output_directory) as temporary_directory:
        database_path = Path(temporary_directory) / "targets.sqlite3"
        with sqlite3.connect(database_path) as connection:
            connection.row_factory = sqlite3.Row
            _create_schema(connection)
            _load_metadata(connection, metadata_source)
            _load_fasta_rows(connection, target_source)
            return _write_outputs(
                connection, output_directory, target_source, metadata_source
            )


__all__ = [
    "CANONICAL_SEQUENCE_POLICY_VERSION",
    "PreparedTarget",
    "TargetPreparationResult",
    "prepare_targets",
]
