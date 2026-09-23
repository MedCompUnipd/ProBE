"""Standalone synchronization of one GOA/UniProt release against ``go.owl``."""

from __future__ import annotations

import argparse
import os
import sqlite3
import tempfile
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from probe.ontology import GO_ROOTS, GeneOntology, TermStatus
from probe.parsing.fasta import FastaParser
from probe.snapshot import ASPECT_NAMESPACES
from probe.source import Source

IGNORED_GOA_DATABASES = frozenset({"ComplexPortal", "RNAcentral"})
DISCARD_REASON_DESCRIPTIONS = {
    "ASPECT_MISMATCH": "GOA aspect does not match the ontology namespace.",
    "DEPRECATED_GO": "GO term is deprecated and has no unique active replacement.",
    "GOA_ONLY": "GOA accession is absent from the UniProt FASTA.",
    "IGNORED_DB": "GOA source database is ComplexPortal or RNAcentral.",
    "OBSOLETE_GO": "GO term is obsolete and has no unique active replacement.",
    "ROOT_GO": "Annotation uses one of the three GO aspect roots.",
    "UNKNOWN_GO": "GO identifier is unknown or cannot be resolved uniquely.",
}


@dataclass(frozen=True, slots=True)
class PreprocessingResult:
    """Deterministic summary of one preprocessing run."""

    ontology_path: Path
    goa_path: Path
    uniprot_fasta_path: Path
    cleaned_goa_path: Path
    filtered_fasta_path: Path
    debug_log_path: Path
    statistics_path: Path | None
    valid_goa_accessions: frozenset[str] | None
    fasta_accessions_written: frozenset[str] | None
    root_only_accessions: tuple[str, ...] | None
    missing_fasta_accessions: tuple[str, ...] | None
    valid_goa_accession_count: int
    fasta_accession_count: int
    root_only_accession_count: int
    missing_fasta_accession_count: int
    shared_accession_count: int
    goa_only_accession_count: int
    uniprot_only_accession_count: int
    retained_annotation_count: int
    remapped_annotation_count: int
    invalid_annotation_count: int
    aspect_mismatch_count: int


@dataclass(frozen=True, slots=True)
class _ResolvedGafRow:
    columns: tuple[str, ...]
    accession: str
    canonical_term_id: str
    remapped: bool


class _DiskAccessionIndex:
    """Bounded-memory temporary index for release-scale accession membership."""

    _TABLES = frozenset({"fasta", "goa", "retained", "valid_root", "missing"})

    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(
            prefix=".probe-preprocess-",
            suffix=".sqlite3",
            dir=directory,
        )
        os.close(descriptor)
        self.path = Path(name)
        self.connection = sqlite3.connect(self.path)
        self.connection.execute("PRAGMA journal_mode=OFF")
        self.connection.execute("PRAGMA synchronous=OFF")
        self.connection.execute("PRAGMA temp_store=FILE")
        self.connection.execute("PRAGMA cache_size=-65536")
        self.connection.execute("PRAGMA locking_mode=EXCLUSIVE")
        for table in sorted(self._TABLES):
            self.connection.execute(
                f"CREATE TABLE {table} (accession TEXT PRIMARY KEY) WITHOUT ROWID"
            )
        self.connection.commit()
        self._pending_writes = 0

    def __enter__(self) -> _DiskAccessionIndex:
        return self

    def __exit__(self, *_error: object) -> None:
        self.connection.close()
        self.path.unlink(missing_ok=True)

    def add_fasta_batch(self, accessions: list[str]) -> None:
        before = self.connection.total_changes
        self.connection.executemany(
            "INSERT OR IGNORE INTO fasta(accession) VALUES (?)",
            ((accession,) for accession in accessions),
        )
        inserted = self.connection.total_changes - before
        if inserted != len(accessions):
            raise ValueError("UniProt FASTA contains a duplicate accession")
        self._checkpoint(len(accessions))

    def contains(self, table: str, accession: str) -> bool:
        self._validate_table(table)
        return (
            self.connection.execute(
                f"SELECT 1 FROM {table} WHERE accession = ?",
                (accession,),
            ).fetchone()
            is not None
        )

    def add(self, table: str, accession: str) -> None:
        self._validate_table(table)
        self.connection.execute(
            f"INSERT OR IGNORE INTO {table}(accession) VALUES (?)",
            (accession,),
        )
        self._checkpoint(1)

    def count(self, table: str) -> int:
        self._validate_table(table)
        row = self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        return int(row[0]) if row else 0

    def root_only_count(self) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) FROM valid_root AS r "
            "WHERE NOT EXISTS "
            "(SELECT 1 FROM retained AS n WHERE n.accession = r.accession)"
        ).fetchone()
        return int(row[0]) if row else 0

    def overlap_counts(self) -> tuple[int, int, int]:
        shared = self.connection.execute(
            "SELECT COUNT(*) FROM goa AS g "
            "WHERE EXISTS (SELECT 1 FROM fasta AS f WHERE f.accession = g.accession)"
        ).fetchone()
        goa_only = self.connection.execute(
            "SELECT COUNT(*) FROM goa AS g "
            "WHERE NOT EXISTS "
            "(SELECT 1 FROM fasta AS f WHERE f.accession = g.accession)"
        ).fetchone()
        uniprot_only = self.connection.execute(
            "SELECT COUNT(*) FROM fasta AS f "
            "WHERE NOT EXISTS "
            "(SELECT 1 FROM goa AS g WHERE g.accession = f.accession)"
        ).fetchone()
        return (
            int(shared[0]) if shared else 0,
            int(goa_only[0]) if goa_only else 0,
            int(uniprot_only[0]) if uniprot_only else 0,
        )

    def ordered(self, table: str) -> tuple[str, ...]:
        self._validate_table(table)
        return tuple(
            row[0]
            for row in self.connection.execute(
                f"SELECT accession FROM {table} ORDER BY accession"
            )
        )

    def ordered_root_only(self) -> tuple[str, ...]:
        return tuple(
            row[0]
            for row in self.connection.execute(
                "SELECT r.accession FROM valid_root AS r "
                "WHERE NOT EXISTS "
                "(SELECT 1 FROM retained AS n WHERE n.accession = r.accession) "
                "ORDER BY r.accession"
            )
        )

    def commit(self) -> None:
        self.connection.commit()
        self._pending_writes = 0

    def _checkpoint(self, writes: int) -> None:
        self._pending_writes += writes
        if self._pending_writes >= 100_000:
            self.commit()

    def _validate_table(self, table: str) -> None:
        if table not in self._TABLES:
            raise ValueError(f"unknown accession-index table: {table}")


def _resolve_gaf_row(
    line: str,
    *,
    line_number: int,
    ontology: GeneOntology,
) -> tuple[_ResolvedGafRow | None, str | None]:
    columns = line.rstrip("\r\n").split("\t")
    if len(columns) != 17:
        raise ValueError(
            f"GAF line {line_number}: expected 17 columns, found {len(columns)}"
        )
    resolution = ontology.resolve(columns[4])
    if not resolution.is_usable or resolution.canonical_id is None:
        reason = {
            TermStatus.OBSOLETE: "OBSOLETE_GO",
            TermStatus.DEPRECATED: "DEPRECATED_GO",
        }.get(resolution.status, "UNKNOWN_GO")
        return None, reason
    term = ontology.term(resolution.canonical_id)
    expected_namespace = ASPECT_NAMESPACES.get(columns[8])
    if (
        term is None
        or expected_namespace is None
        or term.namespace != expected_namespace
    ):
        return None, "ASPECT_MISMATCH"
    original_term_id = columns[4]
    columns[4] = resolution.canonical_id
    return (
        _ResolvedGafRow(
            columns=tuple(columns),
            accession=columns[1],
            canonical_term_id=resolution.canonical_id,
            remapped=original_term_id != resolution.canonical_id,
        ),
        None,
    )


def _index_fasta(source: Source, index: _DiskAccessionIndex) -> None:
    batch: list[str] = []
    for record in FastaParser().iter_records(source, track_duplicates=False):
        batch.append(record.identifier)
        if len(batch) >= 10_000:
            index.add_fasta_batch(batch)
            batch.clear()
    if batch:
        index.add_fasta_batch(batch)
    index.commit()


def _filter_goa(
    source: Source,
    destination: Path,
    *,
    ontology: GeneOntology,
    index: _DiskAccessionIndex,
    discarded_path: Path | None = None,
    changed_path: Path | None = None,
) -> tuple[int, int, int, int, frozenset[str]]:
    retained_count = 0
    remapped_count = 0
    invalid_count = 0
    aspect_mismatch_count = 0
    saw_gaf_version = False
    previous_accession: str | None = None
    previous_in_fasta = False
    previous_marked_missing = False
    previous_marked_retained = False
    previous_marked_root = False
    discard_codes: set[str] = set()

    with ExitStack() as stack:
        input_handle = stack.enter_context(source.open_text())
        output_handle = stack.enter_context(
            destination.open("w", encoding="utf-8", newline="\n")
        )
        discarded_handle: TextIO | None = (
            stack.enter_context(
                discarded_path.open("w", encoding="utf-8", newline="\n")
            )
            if discarded_path is not None
            else None
        )
        changed_handle: TextIO | None = (
            stack.enter_context(changed_path.open("w", encoding="utf-8", newline="\n"))
            if changed_path is not None
            else None
        )

        def discard(original_line: str, code: str) -> None:
            discard_codes.add(code)
            if discarded_handle is not None:
                discarded_handle.write(f"{original_line}\t{code}\n")

        for line_number, raw_line in enumerate(input_handle, start=1):
            line = raw_line.rstrip("\r\n")
            if not line:
                continue
            if line.startswith("!"):
                if line.casefold().startswith("!gaf-version: 2"):
                    saw_gaf_version = True
                output_handle.write(f"{line}\n")
                continue
            columns = line.split("\t")
            if len(columns) != 17:
                raise ValueError(
                    f"GAF line {line_number}: expected 17 columns, found {len(columns)}"
                )
            if columns[0] in IGNORED_GOA_DATABASES:
                discard(line, "IGNORED_DB")
                continue
            accession = columns[1]
            if accession != previous_accession:
                previous_accession = accession
                index.add("goa", accession)
                previous_in_fasta = index.contains("fasta", accession)
                previous_marked_missing = False
                previous_marked_retained = False
                previous_marked_root = False
            if not previous_in_fasta:
                if not previous_marked_missing:
                    index.add("missing", accession)
                    previous_marked_missing = True
                discard(line, "GOA_ONLY")
                continue
            resolved, exclusion = _resolve_gaf_row(
                line,
                line_number=line_number,
                ontology=ontology,
            )
            if resolved is None:
                reason = exclusion or "UNKNOWN_GO"
                discard(line, reason)
                if reason == "ASPECT_MISMATCH":
                    aspect_mismatch_count += 1
                else:
                    invalid_count += 1
                continue
            if resolved.canonical_term_id in GO_ROOTS:
                if not previous_marked_root:
                    index.add("valid_root", resolved.accession)
                    previous_marked_root = True
                discard(line, "ROOT_GO")
                continue
            if not previous_marked_retained:
                index.add("retained", resolved.accession)
                previous_marked_retained = True
            if resolved.remapped:
                remapped_count += 1
            output_handle.write("\t".join(resolved.columns))
            output_handle.write("\n")
            if resolved.remapped and changed_handle is not None:
                changed_handle.write("\t".join(resolved.columns))
                changed_handle.write("\n")
                changed_handle.write(f"{line}\n//\n")
            retained_count += 1

    if not saw_gaf_version:
        raise ValueError("GOA input must declare a GAF 2.x !gaf-version header")
    index.commit()
    return (
        retained_count,
        remapped_count,
        invalid_count,
        aspect_mismatch_count,
        frozenset(discard_codes),
    )


def _write_filtered_fasta(
    source: Source,
    destination: Path,
    *,
    index: _DiskAccessionIndex,
    materialize_accessions: bool,
) -> tuple[int, frozenset[str] | None]:
    written_count = 0
    written = set() if materialize_accessions else None
    with destination.open("w", encoding="utf-8", newline="\n") as output_handle:
        for record in FastaParser().iter_records(source, track_duplicates=False):
            if not index.contains("retained", record.identifier):
                continue
            output_handle.write(f">{record.description}\n")
            for start in range(0, len(record.sequence), 60):
                output_handle.write(f"{record.sequence[start : start + 60]}\n")
            written_count += 1
            if written is not None:
                written.add(record.identifier)
    return written_count, (frozenset(written) if written is not None else None)


def _write_missing_log(
    destination: Path,
    *,
    index: _DiskAccessionIndex,
) -> None:
    with destination.open("w", encoding="utf-8", newline="\n") as handle:
        for row in index.connection.execute(
            "SELECT accession FROM missing ORDER BY accession"
        ):
            handle.write(f"{row[0]}\n")


def _write_statistics_report(
    destination: Path,
    *,
    shared: int,
    goa_only: int,
    uniprot_only: int,
    discard_codes: frozenset[str],
    discarded_path: Path,
    changed_path: Path,
) -> None:
    with destination.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("### STATISTICS\n")
        handle.write("GOA UniProt (shared)\tGOA-only\tUniProt-only\n")
        handle.write(f"{shared}\t{goa_only}\t{uniprot_only}\n\n")
        handle.write("### DISCARDED REASONS LEGEND\n")
        for code in sorted(discard_codes):
            handle.write(f"{code}: {DISCARD_REASON_DESCRIPTIONS[code]}\n")
        handle.write("\n### DISCARDED LINES\n")
        with discarded_path.open("r", encoding="utf-8") as discarded:
            for line in discarded:
                handle.write(line)
        handle.write("\n### CHANGED LINES\n")
        with changed_path.open("r", encoding="utf-8") as changed:
            for line in changed:
                handle.write(line)


def _temporary_report_path(directory: Path, label: str) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix=f".probe-{label}-",
        suffix=".tsv",
        dir=directory,
    )
    os.close(descriptor)
    return Path(name)


class _ReportSpools:
    def __init__(self, directory: Path, *, enabled: bool) -> None:
        self.discarded = (
            _temporary_report_path(directory, "discarded") if enabled else None
        )
        self.changed = _temporary_report_path(directory, "changed") if enabled else None

    def __enter__(self) -> _ReportSpools:
        return self

    def __exit__(self, *_error: object) -> None:
        if self.discarded is not None:
            self.discarded.unlink(missing_ok=True)
        if self.changed is not None:
            self.changed.unlink(missing_ok=True)


def preprocess_release(
    *,
    ontology_path: str | Path,
    goa_path: str | Path,
    uniprot_fasta_path: str | Path,
    cleaned_goa_path: str | Path = "GOA_filtered.gaf",
    filtered_fasta_path: str | Path = "Uniprot_filtered.fasta",
    debug_log_path: str | Path = "missing_accessions.log",
    statistics_path: str | Path | None = None,
    work_directory: str | Path | None = None,
    materialize_accessions: bool = False,
) -> PreprocessingResult:
    """Synchronize a GOA and UniProt FASTA release against one master ``go.owl``.

    Only GOA accessions present in the UniProt FASTA are eligible. GOA rows with
    unresolved, obsolete-without-replacement, cross-aspect, or root terms are
    discarded. Alternate IDs and unique active replacements are rewritten to
    their canonical GO IDs. NOT assertions and all other GAF provenance fields
    are retained for later comparison. The log contains only GOA accessions that
    were absent from the FASTA, one per line.

    Accession membership is stored in a temporary SQLite index under
    ``work_directory`` (the filtered-GOA directory by default). Input files are
    streamed; accession collections are returned only when the explicitly
    opt-in ``materialize_accessions`` argument is true.
    """

    ontology_file = Path(ontology_path)
    goa_file = Path(goa_path)
    fasta_file = Path(uniprot_fasta_path)
    cleaned_goa_file = Path(cleaned_goa_path)
    filtered_fasta_file = Path(filtered_fasta_path)
    debug_log_file = Path(debug_log_path)
    statistics_file = Path(statistics_path) if statistics_path is not None else None
    work_dir = (
        Path(work_directory) if work_directory is not None else cleaned_goa_file.parent
    )
    inputs = {
        ontology_file.resolve(),
        goa_file.resolve(),
        fasta_file.resolve(),
    }
    outputs = [cleaned_goa_file, filtered_fasta_file, debug_log_file]
    if statistics_file is not None:
        outputs.append(statistics_file)
    for output in outputs:
        if output.resolve() in inputs:
            raise ValueError(f"output path would overwrite an input: {output}")
        output.parent.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    ontology = GeneOntology.from_owl(ontology_file)
    goa_source = Source.from_value(goa_file)
    fasta_source = Source.from_value(fasta_file)
    with (
        _ReportSpools(work_dir, enabled=statistics_file is not None) as spools,
        _DiskAccessionIndex(work_dir) as index,
    ):
        _index_fasta(fasta_source, index)
        (
            retained_count,
            remapped_count,
            invalid_count,
            aspect_mismatch_count,
            discard_codes,
        ) = _filter_goa(
            goa_source,
            cleaned_goa_file,
            ontology=ontology,
            index=index,
            discarded_path=spools.discarded,
            changed_path=spools.changed,
        )
        fasta_written_count, fasta_accessions_written = _write_filtered_fasta(
            fasta_source,
            filtered_fasta_file,
            index=index,
            materialize_accessions=materialize_accessions,
        )
        _write_missing_log(debug_log_file, index=index)
        retained_accession_count = index.count("retained")
        missing_accession_count = index.count("missing")
        root_only_accession_count = index.root_only_count()
        (
            shared_accession_count,
            goa_only_accession_count,
            uniprot_only_accession_count,
        ) = index.overlap_counts()
        if (
            statistics_file is not None
            and spools.discarded is not None
            and spools.changed is not None
        ):
            _write_statistics_report(
                statistics_file,
                shared=shared_accession_count,
                goa_only=goa_only_accession_count,
                uniprot_only=uniprot_only_accession_count,
                discard_codes=discard_codes,
                discarded_path=spools.discarded,
                changed_path=spools.changed,
            )
        retained_accessions = (
            frozenset(index.ordered("retained")) if materialize_accessions else None
        )
        missing_accessions = (
            index.ordered("missing") if materialize_accessions else None
        )
        root_only_accessions = (
            index.ordered_root_only() if materialize_accessions else None
        )

    return PreprocessingResult(
        ontology_path=ontology_file,
        goa_path=goa_file,
        uniprot_fasta_path=fasta_file,
        cleaned_goa_path=cleaned_goa_file,
        filtered_fasta_path=filtered_fasta_file,
        debug_log_path=debug_log_file,
        statistics_path=statistics_file,
        valid_goa_accessions=retained_accessions,
        fasta_accessions_written=fasta_accessions_written,
        root_only_accessions=root_only_accessions,
        missing_fasta_accessions=missing_accessions,
        valid_goa_accession_count=retained_accession_count,
        fasta_accession_count=fasta_written_count,
        root_only_accession_count=root_only_accession_count,
        missing_fasta_accession_count=missing_accession_count,
        shared_accession_count=shared_accession_count,
        goa_only_accession_count=goa_only_accession_count,
        uniprot_only_accession_count=uniprot_only_accession_count,
        retained_annotation_count=retained_count,
        remapped_annotation_count=remapped_count,
        invalid_annotation_count=invalid_count,
        aspect_mismatch_count=aspect_mismatch_count,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Synchronize one GOA/UniProt release against go.owl."
    )
    parser.add_argument(
        "--owl",
        type=Path,
        required=True,
        help="path to the pinned go.owl ontology",
    )
    parser.add_argument(
        "--uniprot",
        type=Path,
        required=True,
        help="input UniProt protein FASTA",
    )
    parser.add_argument(
        "--goa",
        type=Path,
        required=True,
        help="input GOA GAF 2.x file",
    )
    parser.add_argument(
        "--new_goa",
        "--new-goa",
        dest="new_goa",
        type=Path,
        required=True,
        help="output filtered GOA file",
    )
    parser.add_argument(
        "--uni_with_go",
        "--uni-with-go",
        dest="uni_with_go",
        type=Path,
        required=True,
        help="output FASTA containing proteins retained in the filtered GOA",
    )
    parser.add_argument(
        "--missing_accessions_log",
        "--missing-accessions-log",
        dest="missing_accessions_log",
        type=Path,
        help=(
            "optional output containing GOA ACCIDs absent from --uniprot; "
            "default: NEW_GOA.missing_accids.log"
        ),
    )
    parser.add_argument(
        "--work_dir",
        "--work-dir",
        dest="work_dir",
        type=Path,
        help=(
            "directory for the temporary disk-backed accession index; "
            "default: directory containing --new_goa"
        ),
    )
    parser.add_argument(
        "--statistics",
        type=Path,
        required=True,
        help="output preprocessing statistics and line-level audit report",
    )
    arguments = parser.parse_args(argv)
    missing_log = arguments.missing_accessions_log or Path(
        f"{arguments.new_goa}.missing_accids.log"
    )
    result = preprocess_release(
        ontology_path=arguments.owl,
        goa_path=arguments.goa,
        uniprot_fasta_path=arguments.uniprot,
        cleaned_goa_path=arguments.new_goa,
        filtered_fasta_path=arguments.uni_with_go,
        debug_log_path=missing_log,
        statistics_path=arguments.statistics,
        work_directory=arguments.work_dir,
    )
    print(f"GOA annotations retained: {result.retained_annotation_count}")
    print(f"GO IDs remapped: {result.remapped_annotation_count}")
    print(f"invalid GO annotations removed: {result.invalid_annotation_count}")
    print(f"GOA accessions retained: {result.valid_goa_accession_count}")
    print(f"root-only accessions removed: {result.root_only_accession_count}")
    print(f"FASTA proteins written: {result.fasta_accession_count}")
    print(f"GOA accessions missing from FASTA: {result.missing_fasta_accession_count}")
    print(f"GOA ∩ UniProt: {result.shared_accession_count}")
    print(f"GOA-only: {result.goa_only_accession_count}")
    print(f"UniProt-only: {result.uniprot_only_accession_count}")
    print(f"missing-accession log: {result.debug_log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
