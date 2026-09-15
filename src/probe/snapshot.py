"""Release-bound GO annotations and ontology consistency checks."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

from probe.ontology import GeneOntology, TermStatus
from probe.parsing.gaf import GafParser
from probe.records import AnnotationRecord
from probe.source import Source
from probe.validation import ValidationReport

ASPECT_NAMESPACES = {
    "F": "molecular_function",
    "P": "biological_process",
    "C": "cellular_component",
}


@dataclass(frozen=True, slots=True)
class AnnotationSnapshot:
    release: str
    annotations: tuple[AnnotationRecord, ...]
    ontology: GeneOntology
    validation: ValidationReport
    annotation_source: Source | None = None

    @classmethod
    def read(
        cls,
        *,
        release: str,
        annotations: Source | str | Path,
        ontology: GeneOntology | Source | str | Path,
        subjects: Collection[str] | None = None,
        strict: bool = True,
    ) -> AnnotationSnapshot:
        report = ValidationReport()
        annotation_source = Source.from_value(annotations)
        go = (
            ontology
            if isinstance(ontology, GeneOntology)
            else GeneOntology.from_owl(ontology, strict=False, report=report)
        )
        wanted = frozenset(subjects) if subjects is not None else None
        parsed = tuple(
            annotation
            for annotation in GafParser().iter_records(
                annotation_source, report=report, strict=False
            )
            if wanted is None
            or annotation.subject_id in wanted
            or annotation.qualified_subject_id in wanted
        )
        snapshot = cls(release, parsed, go, report, annotation_source)
        snapshot._validate_annotations()
        if strict:
            report.raise_for_errors()
        return snapshot

    def _validate_annotations(self) -> None:
        for annotation in self.annotations:
            resolution = self.ontology.resolve(annotation.term_id)
            if not resolution.is_usable:
                self.validation.warning(
                    "ANNOTATION_TERM_EXCLUDED",
                    f"{annotation.term_id} is {resolution.status.value} "
                    "in the pinned ontology",
                    source=annotation.source,
                    line=annotation.line,
                )
                continue
            if resolution.status is TermStatus.REPLACED:
                self.validation.warning(
                    "ANNOTATION_TERM_REPLACED",
                    f"{annotation.term_id} is replaced by {resolution.canonical_id}",
                    source=annotation.source,
                    line=annotation.line,
                )
            term = self.ontology.term(annotation.term_id)
            if term is None:
                continue
            expected = ASPECT_NAMESPACES.get(annotation.aspect)
            if term.namespace and expected and term.namespace != expected:
                self.validation.error(
                    "ANNOTATION_ASPECT_MISMATCH",
                    f"{annotation.term_id} is {term.namespace}, not {expected}",
                    source=annotation.source,
                    line=annotation.line,
                )

    def __len__(self) -> int:
        return len(self.annotations)
