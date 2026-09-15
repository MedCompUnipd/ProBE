"""Streaming parser for protein FASTA files."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from probe.records import SequenceRecord
from probe.source import Source
from probe.validation import ValidationReport

PROTEIN_ALPHABET = frozenset("ACDEFGHIKLMNPQRSTVWYBXZJUO")


def _identifier(header: str) -> str:
    token = header.split(maxsplit=1)[0]
    parts = token.split("|")
    if len(parts) >= 3 and parts[0].lower() in {"sp", "tr"}:
        return parts[1]
    return token


@dataclass(frozen=True, slots=True)
class FastaParser:
    """Parse normalized protein records without loading the whole file."""

    alphabet: frozenset[str] = PROTEIN_ALPHABET

    def iter_records(
        self,
        source: Source | str | Path,
        *,
        report: ValidationReport | None = None,
        strict: bool = True,
    ) -> Iterator[SequenceRecord]:
        source = Source.from_value(source)
        report = report if report is not None else ValidationReport()
        seen: set[str] = set()
        header: str | None = None
        sequence_parts: list[str] = []
        header_line: int | None = None

        def finish_record() -> SequenceRecord | None:
            if header is None:
                return None
            identifier = _identifier(header)
            sequence = "".join(sequence_parts).replace(" ", "").upper()
            if not identifier:
                report.error(
                    "EMPTY_FASTA_IDENTIFIER",
                    "FASTA header has no identifier",
                    source=source.name,
                    line=header_line,
                )
            elif identifier in seen:
                report.error(
                    "DUPLICATE_FASTA_IDENTIFIER",
                    f"duplicate FASTA identifier {identifier!r}",
                    source=source.name,
                    line=header_line,
                )
            if not sequence:
                report.error(
                    "EMPTY_FASTA_SEQUENCE",
                    f"record {identifier!r} has no sequence",
                    source=source.name,
                    line=header_line,
                )
            invalid = sorted(set(sequence) - self.alphabet)
            if invalid:
                report.error(
                    "INVALID_AMINO_ACID",
                    f"record {identifier!r} contains {''.join(invalid)!r}",
                    source=source.name,
                    line=header_line,
                )
            if strict:
                report.raise_for_errors()
            if not identifier or not sequence or invalid or identifier in seen:
                return None
            seen.add(identifier)
            return SequenceRecord(
                identifier=identifier,
                sequence=sequence,
                description=header,
                source=source.name,
                line=header_line,
            )

        with source.open_text() as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                if line.startswith(">"):
                    if record := finish_record():
                        yield record
                    header = line[1:].strip()
                    sequence_parts = []
                    header_line = line_number
                elif header is None:
                    report.error(
                        "FASTA_SEQUENCE_BEFORE_HEADER",
                        "sequence data appears before the first header",
                        source=source.name,
                        line=line_number,
                    )
                    if strict:
                        report.raise_for_errors()
                else:
                    sequence_parts.append("".join(line.split()))

        if record := finish_record():
            yield record

    def parse(
        self,
        source: Source | str | Path,
        *,
        report: ValidationReport | None = None,
        strict: bool = True,
    ) -> tuple[SequenceRecord, ...]:
        return tuple(self.iter_records(source, report=report, strict=strict))
