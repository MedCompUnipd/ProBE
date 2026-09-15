"""Streaming reader for Gene Ontology Annotation Format 2.x."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from probe.records import AnnotationRecord
from probe.source import Source
from probe.validation import ValidationReport

VALID_ASPECTS = frozenset({"F", "P", "C"})


def _parts(value: str) -> tuple[str, ...]:
    return tuple(part for part in value.split("|") if part)


@dataclass(slots=True)
class GafParser:
    """Parse GAF rows while retaining their complete assertion provenance."""

    metadata: dict[str, str] = field(default_factory=dict, init=False)

    def iter_records(
        self,
        source: Source | str | Path,
        *,
        report: ValidationReport | None = None,
        strict: bool = True,
    ) -> Iterator[AnnotationRecord]:
        source = Source.from_value(source)
        report = report if report is not None else ValidationReport()
        self.metadata = {}
        saw_version = False

        with source.open_text() as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                line = raw_line.rstrip("\r\n")
                if not line:
                    continue
                if line.startswith("!"):
                    key, separator, value = line[1:].partition(":")
                    if separator:
                        self.metadata[key.strip().lower()] = value.strip()
                        if key.strip().lower() == "gaf-version":
                            saw_version = True
                    continue

                columns = line.split("\t")
                if len(columns) != 17:
                    report.error(
                        "INVALID_GAF_COLUMN_COUNT",
                        f"expected 17 columns, found {len(columns)}",
                        source=source.name,
                        line=line_number,
                    )
                    if strict:
                        report.raise_for_errors()
                    continue

                relation_parts = _parts(columns[3])
                negated = "NOT" in relation_parts
                relations = tuple(part for part in relation_parts if part != "NOT")
                relation = relations[0] if len(relations) == 1 else ""
                if len(relations) > 1:
                    report.error(
                        "MULTIPLE_GAF_RELATIONS",
                        f"expected one relation, found {relations!r}",
                        source=source.name,
                        line=line_number,
                    )
                if columns[8] not in VALID_ASPECTS:
                    report.error(
                        "INVALID_GAF_ASPECT",
                        f"unknown GO aspect {columns[8]!r}",
                        source=source.name,
                        line=line_number,
                    )
                if not columns[4].startswith("GO:"):
                    report.error(
                        "INVALID_GO_IDENTIFIER",
                        f"invalid GO term identifier {columns[4]!r}",
                        source=source.name,
                        line=line_number,
                    )
                if not columns[6]:
                    report.error(
                        "MISSING_EVIDENCE_CODE",
                        "annotation has no evidence code",
                        source=source.name,
                        line=line_number,
                    )
                if columns[13] and (len(columns[13]) != 8 or not columns[13].isdigit()):
                    report.warning(
                        "NONSTANDARD_GAF_DATE",
                        f"expected YYYYMMDD, found {columns[13]!r}",
                        source=source.name,
                        line=line_number,
                    )
                if strict:
                    report.raise_for_errors()
                if any(
                    issue.line == line_number
                    for issue in report.errors
                    if issue.source == source.name
                ):
                    continue

                yield AnnotationRecord(
                    database=columns[0],
                    subject_id=columns[1],
                    symbol=columns[2],
                    relation=relation,
                    term_id=columns[4],
                    negated=negated,
                    references=_parts(columns[5]),
                    evidence=columns[6],
                    with_from=_parts(columns[7]),
                    aspect=columns[8],
                    name=columns[9],
                    synonyms=_parts(columns[10]),
                    object_type=columns[11],
                    taxa=_parts(columns[12]),
                    date=columns[13],
                    assigned_by=columns[14],
                    extensions=_parts(columns[15]),
                    gene_product_form_id=columns[16],
                    source=source.name,
                    line=line_number,
                )

        if not saw_version:
            report.error(
                "MISSING_GAF_VERSION",
                "GAF header does not declare !gaf-version",
                source=source.name,
            )
        elif not self.metadata["gaf-version"].startswith("2"):
            report.error(
                "UNSUPPORTED_GAF_VERSION",
                f"only GAF 2.x is supported, found {self.metadata['gaf-version']!r}",
                source=source.name,
            )
        if strict:
            report.raise_for_errors()

    def parse(
        self,
        source: Source | str | Path,
        *,
        report: ValidationReport | None = None,
        strict: bool = True,
    ) -> tuple[AnnotationRecord, ...]:
        return tuple(self.iter_records(source, report=report, strict=strict))
