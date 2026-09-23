"""Assign internal target IDs and match exact sequences to UniProt accessions."""

from __future__ import annotations

import argparse
import hashlib
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from probe.source import Source

IUPAC_PROTEIN_SYMBOLS = frozenset("ACDEFGHIKLMNPQRSTVWYBJOUXZ")
NORMALIZATION_POLICY_VERSION = "1"


class TerminalStopPolicy(StrEnum):
    """Explicit treatment of one terminal translation-stop marker."""

    PRESERVE = "preserve"
    STRIP = "strip"


@dataclass(frozen=True, slots=True)
class SequenceNormalizationPolicy:
    terminal_stop: TerminalStopPolicy
    version: str = NORMALIZATION_POLICY_VERSION


@dataclass(frozen=True, slots=True)
class NormalizedProteinSequence:
    original_sequence: str
    normalized_sequence: str
    ambiguous_symbols: frozenset[str]
    policy: SequenceNormalizationPolicy

    @property
    def length(self) -> int:
        return len(self.normalized_sequence)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.normalized_sequence.encode("ascii")).hexdigest()


@dataclass(frozen=True, slots=True)
class TargetMappingRecord:
    original_header: str
    internal_id: str
    uniprot_accessions: tuple[str, ...]
    protein_length: int
    sequence_sha256: str
    original_sequence: str
    normalized_sequence: str
    ambiguous_symbols: frozenset[str]
    normalization_policy_version: str


@dataclass(frozen=True, slots=True)
class TargetMappingResult:
    records: tuple[TargetMappingRecord, ...]
    mapping_path: Path
    internal_fasta_path: Path
    normalization_policy: SequenceNormalizationPolicy


@dataclass(frozen=True, slots=True)
class _FastaEntry:
    header: str
    sequence: str
    line: int


@dataclass(slots=True)
class _TargetWorkRecord:
    original_header: str
    internal_id: str
    normalized: NormalizedProteinSequence
    accessions: set[str] = field(default_factory=set)


def normalize_protein_sequence(
    sequence: str,
    *,
    policy: SequenceNormalizationPolicy,
) -> NormalizedProteinSequence:
    """Normalize symmetrically without substituting ambiguous protein symbols."""

    normalized = "".join(sequence.split()).upper()
    if not normalized:
        raise ValueError("protein sequence is empty after whitespace removal")
    if "*" in normalized[:-1]:
        raise ValueError("internal '*' is not permitted in a protein sequence")
    if normalized.endswith("*") and policy.terminal_stop is TerminalStopPolicy.STRIP:
        normalized = normalized[:-1]
    if not normalized:
        raise ValueError("protein sequence contains only a terminal '*'")
    invalid = sorted(set(normalized) - IUPAC_PROTEIN_SYMBOLS - {"*"})
    if invalid:
        raise ValueError(f"unsupported protein symbols: {''.join(invalid)}")
    ambiguous = frozenset(set(normalized) & set("BJXZ"))
    return NormalizedProteinSequence(sequence, normalized, ambiguous, policy)


def _iter_fasta(source: Source) -> Iterator[_FastaEntry]:
    header: str | None = None
    header_line = 0
    sequence_parts: list[str] = []

    def finish() -> _FastaEntry | None:
        if header is None:
            return None
        sequence = "".join(sequence_parts)
        if not sequence:
            raise ValueError(f"FASTA record at line {header_line} has no sequence")
        return _FastaEntry(header, sequence, header_line)

    with source.open_text() as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.rstrip("\r\n")
            if not line.strip():
                continue
            if line.startswith(">"):
                if entry := finish():
                    yield entry
                header = line[1:]
                if not header:
                    raise ValueError(f"empty FASTA header at line {line_number}")
                header_line = line_number
                sequence_parts = []
            elif header is None:
                raise ValueError(
                    f"FASTA sequence precedes first header at line {line_number}"
                )
            else:
                sequence_parts.append(line)
    if entry := finish():
        yield entry


def _uniprot_accession(header: str, *, line: int) -> str:
    token = header.split(maxsplit=1)[0]
    parts = token.split("|")
    if len(parts) < 3 or not parts[0] or not parts[1]:
        raise ValueError(
            f"UniProt FASTA header at line {line} must use db|ACCID|entry_name"
        )
    return parts[1]


def _quoted_header(header: str) -> str:
    sanitized = header.replace("\t", " ").replace('"', '""')
    return f'"{sanitized}"'


def _write_fasta_record(handle, identifier: str, sequence: str) -> None:
    handle.write(f">{identifier}\n")
    for start in range(0, len(sequence), 60):
        handle.write(f"{sequence[start : start + 60]}\n")


def map_target_sequences(
    *,
    target_fasta: str | Path,
    uniprot_fasta: str | Path,
    mapping_path: str | Path,
    internal_fasta_path: str | Path,
    normalization_policy: SequenceNormalizationPolicy,
) -> TargetMappingResult:
    """Assign deterministic target IDs and find exact UniProt sequence matches."""

    target_source = Source.from_value(target_fasta)
    uniprot_source = Source.from_value(uniprot_fasta)
    mapping_file = Path(mapping_path)
    internal_fasta_file = Path(internal_fasta_path)
    inputs = {target_source.path.resolve(), uniprot_source.path.resolve()}
    outputs = {mapping_file.resolve(), internal_fasta_file.resolve()}
    if len(outputs) != 2:
        raise ValueError("mapping and internal FASTA outputs must be different files")
    for output in (mapping_file, internal_fasta_file):
        if output.resolve() in inputs:
            raise ValueError(f"output path would overwrite an input: {output}")
        output.parent.mkdir(parents=True, exist_ok=True)

    records: list[_TargetWorkRecord] = []
    target_index: dict[tuple[str, int], dict[str, list[_TargetWorkRecord]]] = (
        defaultdict(lambda: defaultdict(list))
    )
    seen_headers: set[str] = set()
    with internal_fasta_file.open("w", encoding="utf-8", newline="\n") as output:
        for number, entry in enumerate(_iter_fasta(target_source), start=1):
            if number > 999_999_999:
                raise ValueError("target count exceeds the T + 9 digit ID space")
            output_header = entry.header.replace("\t", " ")
            if output_header in seen_headers:
                raise ValueError(f"duplicate target header: {output_header!r}")
            seen_headers.add(output_header)
            normalized = normalize_protein_sequence(
                entry.sequence,
                policy=normalization_policy,
            )
            internal_id = f"T{number:09d}"
            record = _TargetWorkRecord(
                entry.header,
                internal_id,
                normalized,
            )
            records.append(record)
            key = normalized.sha256, normalized.length
            target_index[key][normalized.normalized_sequence].append(record)
            _write_fasta_record(
                output,
                internal_id,
                normalized.normalized_sequence,
            )

    for entry in _iter_fasta(uniprot_source):
        accession = _uniprot_accession(entry.header, line=entry.line)
        normalized = normalize_protein_sequence(
            entry.sequence,
            policy=normalization_policy,
        )
        key = normalized.sha256, normalized.length
        by_sequence = target_index.get(key)
        if by_sequence is None:
            continue
        matched_targets = by_sequence.get(normalized.normalized_sequence)
        if not matched_targets:
            continue
        for target in matched_targets:
            target.accessions.add(accession)

    final_records = tuple(
        TargetMappingRecord(
            original_header=record.original_header,
            internal_id=record.internal_id,
            uniprot_accessions=tuple(sorted(record.accessions)),
            protein_length=record.normalized.length,
            sequence_sha256=record.normalized.sha256,
            original_sequence=record.normalized.original_sequence,
            normalized_sequence=record.normalized.normalized_sequence,
            ambiguous_symbols=record.normalized.ambiguous_symbols,
            normalization_policy_version=normalization_policy.version,
        )
        for record in records
    )
    with mapping_file.open("w", encoding="utf-8", newline="\n") as output:
        for record in final_records:
            output.write(
                "\t".join(
                    (
                        _quoted_header(record.original_header),
                        record.internal_id,
                        ",".join(record.uniprot_accessions),
                        str(record.protein_length),
                    )
                )
            )
            output.write("\n")

    return TargetMappingResult(
        final_records,
        mapping_file,
        internal_fasta_file,
        normalization_policy,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Assign internal target IDs and match exact UniProt sequences."
    )
    parser.add_argument("--targets", type=Path, required=True)
    parser.add_argument("--uniprot", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument(
        "--internal_fasta", "--internal-fasta", type=Path, required=True
    )
    parser.add_argument(
        "--terminal_stop",
        "--terminal-stop",
        type=TerminalStopPolicy,
        choices=tuple(TerminalStopPolicy),
        required=True,
    )
    arguments = parser.parse_args(argv)
    result = map_target_sequences(
        target_fasta=arguments.targets,
        uniprot_fasta=arguments.uniprot,
        mapping_path=arguments.mapping,
        internal_fasta_path=arguments.internal_fasta,
        normalization_policy=SequenceNormalizationPolicy(arguments.terminal_stop),
    )
    matched = sum(bool(record.uniprot_accessions) for record in result.records)
    print(f"targets: {len(result.records)}")
    print(f"targets with exact UniProt matches: {matched}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
