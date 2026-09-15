"""Immutable records at ProBE's input boundary."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SequenceRecord:
    identifier: str
    sequence: str
    description: str = ""
    source: str | None = None
    line: int | None = None


@dataclass(frozen=True, slots=True)
class AnnotationRecord:
    """A lossless representation of one GAF 2.x association row."""

    database: str
    subject_id: str
    symbol: str
    relation: str
    term_id: str
    negated: bool
    references: tuple[str, ...]
    evidence: str
    with_from: tuple[str, ...]
    aspect: str
    name: str
    synonyms: tuple[str, ...]
    object_type: str
    taxa: tuple[str, ...]
    date: str
    assigned_by: str
    extensions: tuple[str, ...]
    gene_product_form_id: str
    source: str | None = None
    line: int | None = None

    @property
    def qualified_subject_id(self) -> str:
        return f"{self.database}:{self.subject_id}"
