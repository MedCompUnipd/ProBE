"""Public notebook API for ProBE."""

from probe.comparison import (
    AnnotationChange,
    ChangeKind,
    ComparisonResult,
    compare_annotations,
)
from probe.evidence import EvidenceCategory, EvidencePolicy
from probe.identity import (
    IdentityMap,
    SequenceAlias,
    SequenceDataset,
    SequenceIndex,
    SequenceMatch,
    normalize_sequence,
    sequence_digest,
)
from probe.ontology import GeneOntology, OntologyTerm, TermProjection
from probe.snapshot import AnnotationSnapshot
from probe.validation import (
    Severity,
    ValidationError,
    ValidationIssue,
    ValidationReport,
)

__all__ = [
    "AnnotationChange",
    "AnnotationSnapshot",
    "ChangeKind",
    "ComparisonResult",
    "EvidenceCategory",
    "EvidencePolicy",
    "GeneOntology",
    "IdentityMap",
    "OntologyTerm",
    "SequenceAlias",
    "SequenceDataset",
    "SequenceIndex",
    "SequenceMatch",
    "Severity",
    "TermProjection",
    "ValidationError",
    "ValidationIssue",
    "ValidationReport",
    "compare_annotations",
    "normalize_sequence",
    "sequence_digest",
]
