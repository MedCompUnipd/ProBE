"""Local, restartable preparation of declared full-length target proteins."""

from __future__ import annotations

import csv
import json
import os
import sqlite3
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from probe.source import Source
from probe.target_mapping import (
    SequenceNormalizationPolicy,
    TerminalStopPolicy,
    normalize_protein_sequence,
)

NORMALIZATION_POLICY = SequenceNormalizationPolicy(TerminalStopPolicy.PRESERVE)
TARGETS_FILENAME = "01_targets.tsv"
REJECTED_FILENAME = "01_rejected.tsv"
SUMMARY_FILENAME = "01_summary.json"


@dataclass(frozen=True, slots=True)
class TargetPreparationResult:
    targets_path: Path
    rejected_path: Path
    summary_path: Path
    accepted_count: int
    rejected_count: int


@dataclass(frozen=True, slots=True)
class PreparedTarget:
    """One accepted target retained for downstream exact sequence comparison."""

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


def _iter_targets(source: Source) -> Iterator[_FastaTarget]:
    header: str | None = None
    header_line = 0
    sequence_parts: list[str] = []

    def finish() -> _FastaTarget | None:
        if header is None:
            return None
        target_id = header.split(maxsplit=1)[0]
        return _FastaTarget(target_id, "".join(sequence_parts), header_line)

    with source.open_text() as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.rstrip("\r\n")
            if not line.strip():
                continue
            if line.startswith(">"):
                if target := finish():
                    yield target
                header = line[1:].strip()
                header_line = line_number
                sequence_parts = []
            elif header is None:
                raise ValueError(
                    "FASTA sequence precedes first header at "
                    f"{source.name}:{line_number}"
                )
            else:
                sequence_parts.append(line)
    if target := finish():
        yield target


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE metadata (
            target_id TEXT PRIMARY KEY,
            raw_taxid TEXT,
            optional_accession TEXT,
            status TEXT NOT NULL,
            reason TEXT NOT NULL
        );
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
            source_line INTEGER NOT NULL
        );
        CREATE INDEX fasta_rows_target_id_idx ON fasta_rows (target_id);
        """
    )


def _load_metadata(connection: sqlite3.Connection, source: Source) -> None:
    with source.open_text() as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        required = {"target_id", "ncbi_taxid"}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError(
                "target metadata TSV requires target_id and ncbi_taxid columns"
            )
        for row in reader:
            target_id = row["target_id"] or ""
            raw_taxid = row["ncbi_taxid"]
            optional_accession = row.get("uniprot_accession") or ""
            if not target_id:
                raise ValueError("metadata row has no target_id")
            if not raw_taxid.strip():
                status, reason = "MISSING_TAXID", "metadata row has no ncbi_taxid"
            else:
                status, reason = "VALID", ""
            existing = connection.execute(
                """
                SELECT raw_taxid, optional_accession FROM metadata WHERE target_id = ?
                """,
                (target_id,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO metadata (
                        target_id, raw_taxid, optional_accession, status, reason
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (target_id, raw_taxid, optional_accession, status, reason),
                )
            else:
                duplicate_status = (
                    "DUPLICATE_METADATA"
                    if (existing[0], existing[1]) == (raw_taxid, optional_accession)
                    else "CONFLICTING_METADATA"
                )
                connection.execute(
                    "UPDATE metadata SET status = ?, reason = ? WHERE target_id = ?",
                    (
                        duplicate_status,
                        "target_id occurs more than once in metadata",
                        target_id,
                    ),
                )


def _load_fasta_rows(connection: sqlite3.Connection, source: Source) -> None:
    for record_number, target in enumerate(_iter_targets(source), start=1):
        status = "VALID"
        reason = ""
        sequence_length: int | None = None
        sequence_sha256: str | None = None
        normalized_sequence: str | None = None
        raw_taxid: str | None = None
        optional_accession: str | None = None
        if not target.target_id:
            status, reason = "INVALID_TARGET_ID", "FASTA header has no target_id"
        else:
            try:
                normalized = normalize_protein_sequence(
                    target.sequence, policy=NORMALIZATION_POLICY
                )
            except ValueError as error:
                status, reason = "INVALID_SEQUENCE", str(error)
            else:
                normalized_sequence = normalized.normalized_sequence
                sequence_length = normalized.length
                sequence_sha256 = normalized.sha256
            metadata = connection.execute(
                """
                SELECT raw_taxid, optional_accession, status, reason
                FROM metadata WHERE target_id = ?
                """,
                (target.target_id,),
            ).fetchone()
            if metadata is None:
                if status == "VALID":
                    status, reason = "MISSING_METADATA", "no metadata row for target_id"
            else:
                raw_taxid, optional_accession = metadata[0], metadata[1]
                if status == "VALID" and metadata[2] != "VALID":
                    status, reason = metadata[2], metadata[3]
        connection.execute(
            """
            INSERT INTO fasta_rows (
                record_number, target_id, raw_taxid, optional_accession,
                normalized_sequence, sequence_length, sequence_sha256, status,
                reason, source_line
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                target.line,
            ),
        )
    connection.execute(
        """
        UPDATE fasta_rows
        SET status = 'DUPLICATE_TARGET_ID',
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
                target = PreparedTarget(
                    target_id=row["target_id"],
                    raw_taxid=row["raw_taxid"],
                    optional_accession=row["optional_accession"],
                    normalized_sequence=row["normalized_sequence"],
                    sequence_length=row["sequence_length"],
                    sequence_sha256=row["sequence_sha256"],
                    normalization_policy_version=NORMALIZATION_POLICY.version,
                )
                writer.writerow(
                    (
                        target.target_id,
                        target.raw_taxid,
                        target.optional_accession,
                        target.normalized_sequence,
                        target.sequence_length,
                        target.sequence_sha256,
                        target.normalization_policy_version,
                    )
                )
            output.flush()
            os.fsync(output.fileno())
        with temporary_rejected.open("w", encoding="utf-8", newline="\n") as output:
            writer = csv.writer(output, delimiter="\t", lineterminator="\n")
            writer.writerow(
                ("target_id", "raw_taxid", "source_line", "status", "reason")
            )
            for row in connection.execute(
                """
                SELECT target_id, raw_taxid, source_line, status, reason
                FROM fasta_rows WHERE status != 'VALID' ORDER BY record_number
                """
            ):
                writer.writerow(
                    (
                        row["target_id"],
                        row["raw_taxid"] or "",
                        row["source_line"],
                        row["status"],
                        row["reason"],
                    )
                )
            output.flush()
            os.fsync(output.fileno())
        accepted_count = connection.execute(
            "SELECT COUNT(*) FROM fasta_rows WHERE status = 'VALID'"
        ).fetchone()[0]
        rejected_count = connection.execute(
            "SELECT COUNT(*) FROM fasta_rows WHERE status != 'VALID'"
        ).fetchone()[0]
        summary = {
            "accepted_count": accepted_count,
            "metadata_path": metadata_source.name,
            "normalization_policy_version": NORMALIZATION_POLICY.version,
            "rejected_count": rejected_count,
            "target_fasta_path": target_source.name,
            "target_sequence_declared_full_length": True,
            "total_fasta_records": accepted_count + rejected_count,
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
    """Prepare declared full-length FASTA targets with local metadata only."""

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


__all__ = ["PreparedTarget", "TargetPreparationResult", "prepare_targets"]
