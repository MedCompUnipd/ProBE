"""Streaming parser for UniProtKB text (``.dat``) records."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from probe.parsing.fasta import PROTEIN_ALPHABET
from probe.records import UniProtRecord, UniProtSection
from probe.source import Source
from probe.validation import ValidationReport

_ID_PATTERN = re.compile(r"^(\S+)\s+(Reviewed|Unreviewed);\s+(\d+)\s+AA\.$")
_SQ_LENGTH_PATTERN = re.compile(r"^SEQUENCE\s+(\d+)\s+AA;")
_OX_TAXID_PATTERN = re.compile(r"(?:^|;\s*)NCBI_TaxID=([^;]+)")
_GN_FIELD_PATTERN = re.compile(
    r"(?:^|;\s*)(Name|Synonyms|OrderedLocusNames|ORFNames)=([^;]+)"
)
_EVIDENCE_SUFFIX_PATTERN = re.compile(r"\s*\{[^{}]*\}\s*$")


@dataclass(slots=True)
class _RecordBuilder:
    start_line: int
    entry_name: str | None = None
    id_length: int | None = None
    accessions: list[str] = field(default_factory=list)
    raw_taxids: list[str] = field(default_factory=list)
    gene_lines: list[str] = field(default_factory=list)
    description_lines: list[str] = field(default_factory=list)
    sq_length: int | None = None
    sequence_parts: list[str] = field(default_factory=list)
    in_sequence: bool = False


def _gene_values(value: str) -> tuple[str, ...]:
    values = []
    for part in value.split(","):
        cleaned = _EVIDENCE_SUFFIX_PATTERN.sub("", part).strip()
        if cleaned:
            values.append(cleaned)
    return tuple(values)


def _gene_metadata(
    lines: list[str],
) -> tuple[str | None, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    names: list[str] = []
    synonyms: list[str] = []
    ordered_locus_names: list[str] = []
    orf_names: list[str] = []
    for field_name, raw_value in _GN_FIELD_PATTERN.findall(" ".join(lines)):
        values = _gene_values(raw_value)
        if field_name == "Name":
            names.extend(values)
        elif field_name == "Synonyms":
            synonyms.extend(values)
        elif field_name == "OrderedLocusNames":
            ordered_locus_names.extend(values)
        else:
            orf_names.extend(values)
    return (
        names[0] if names else None,
        tuple(synonyms),
        tuple(ordered_locus_names),
        tuple(orf_names),
    )


def _is_fragment(description_lines: list[str]) -> bool:
    description = " ".join(description_lines)
    marker = description.find("Flags:")
    return marker >= 0 and bool(re.search(r"\bFragments?;", description[marker:]))


@dataclass(frozen=True, slots=True)
class UniProtDatParser:
    """Parse one caller-identified UniProtKB section without buffering the file."""

    section: UniProtSection

    def iter_records(
        self,
        source: Source | str | Path,
        *,
        report: ValidationReport | None = None,
        strict: bool = True,
    ) -> Iterator[UniProtRecord]:
        source = Source.from_value(source)
        report = report if report is not None else ValidationReport()
        builder: _RecordBuilder | None = None

        def error(code: str, message: str, *, line: int | None = None) -> None:
            report.error(code, message, source=source.name, line=line)

        def finish_record() -> UniProtRecord | None:
            nonlocal builder
            if builder is None:
                return None
            current = builder
            builder = None
            error_count = len(report.errors)

            if current.entry_name is None or current.id_length is None:
                error(
                    "MISSING_UNIPROT_ID",
                    "record has no valid ID line",
                    line=current.start_line,
                )
            if not current.accessions:
                error(
                    "MISSING_UNIPROT_ACCESSION",
                    "record has no AC accession",
                    line=current.start_line,
                )
            if len(current.raw_taxids) != 1:
                error(
                    "INVALID_UNIPROT_TAXID_COUNT",
                    "record must contain exactly one NCBI_TaxID in OX",
                    line=current.start_line,
                )
            if current.sq_length is None:
                error(
                    "MISSING_UNIPROT_SEQUENCE_HEADER",
                    "record has no valid SQ line",
                    line=current.start_line,
                )

            # Import lazily to reuse the authoritative identity implementation
            # without creating a package-import cycle through probe.parsing.
            from probe.identity import normalize_sequence, sequence_digest

            sequence = normalize_sequence("".join(current.sequence_parts))
            if not sequence:
                error(
                    "EMPTY_UNIPROT_SEQUENCE",
                    "record has no amino-acid sequence",
                    line=current.start_line,
                )
            invalid = sorted(set(sequence) - PROTEIN_ALPHABET)
            if invalid:
                error(
                    "INVALID_AMINO_ACID",
                    f"record contains {''.join(invalid)!r}",
                    line=current.start_line,
                )
            lengths = (current.id_length, current.sq_length, len(sequence))
            if None not in lengths and len(set(lengths)) != 1:
                error(
                    "UNIPROT_SEQUENCE_LENGTH_MISMATCH",
                    "ID, SQ, and observed sequence lengths differ: "
                    f"{current.id_length}, {current.sq_length}, {len(sequence)}",
                    line=current.start_line,
                )

            if strict:
                report.raise_for_errors()
            if len(report.errors) != error_count:
                return None

            gene_name, synonyms, ordered_locus_names, orf_names = _gene_metadata(
                current.gene_lines
            )
            assert current.entry_name is not None
            assert current.accessions
            assert len(current.raw_taxids) == 1
            return UniProtRecord(
                primary_accession=current.accessions[0],
                secondary_accessions=tuple(current.accessions[1:]),
                entry_name=current.entry_name,
                sequence=sequence,
                sequence_length=len(sequence),
                sequence_sha256=sequence_digest(sequence),
                raw_taxid=current.raw_taxids[0],
                section=self.section,
                gene_name=gene_name,
                gene_synonyms=synonyms,
                ordered_locus_names=ordered_locus_names,
                orf_names=orf_names,
                is_fragment=_is_fragment(current.description_lines),
                source=source.name,
                line=current.start_line,
            )

        with source.open_text() as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                line = raw_line.rstrip("\r\n")
                if line == "//":
                    if record := finish_record():
                        yield record
                    continue
                if not line:
                    continue
                if builder is None:
                    builder = _RecordBuilder(start_line=line_number)

                if builder.in_sequence:
                    builder.sequence_parts.extend(
                        token for token in line.split() if not token.isdigit()
                    )
                    continue

                code = line[:2]
                value = line[5:].strip() if len(line) > 5 else ""
                if code == "ID":
                    match = _ID_PATTERN.fullmatch(value)
                    if match is None:
                        error(
                            "INVALID_UNIPROT_ID",
                            f"invalid ID line {value!r}",
                            line=line_number,
                        )
                    else:
                        builder.entry_name = match.group(1)
                        builder.id_length = int(match.group(3))
                elif code == "AC":
                    builder.accessions.extend(
                        accession.strip()
                        for accession in value.split(";")
                        if accession.strip()
                    )
                elif code == "DE":
                    builder.description_lines.append(value)
                elif code == "GN":
                    builder.gene_lines.append(value)
                elif code == "OX":
                    builder.raw_taxids.extend(
                        taxid.strip() for taxid in _OX_TAXID_PATTERN.findall(value)
                    )
                elif code == "SQ":
                    match = _SQ_LENGTH_PATTERN.match(value)
                    if match is None:
                        error(
                            "INVALID_UNIPROT_SEQUENCE_HEADER",
                            f"invalid SQ line {value!r}",
                            line=line_number,
                        )
                    else:
                        builder.sq_length = int(match.group(1))
                    builder.in_sequence = True

        if builder is not None:
            error(
                "UNTERMINATED_UNIPROT_RECORD",
                "record is not terminated by //",
                line=builder.start_line,
            )
            if strict:
                report.raise_for_errors()

    def parse(
        self,
        source: Source | str | Path,
        *,
        report: ValidationReport | None = None,
        strict: bool = True,
    ) -> tuple[UniProtRecord, ...]:
        return tuple(self.iter_records(source, report=report, strict=strict))


__all__ = ["UniProtDatParser", "UniProtSection"]
