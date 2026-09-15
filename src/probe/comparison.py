"""Compare annotation snapshots over exact sequence identities."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum

from probe.evidence import EvidencePolicy
from probe.identity import IdentityMap
from probe.ontology import TermResolution, TermStatus
from probe.records import AnnotationRecord
from probe.snapshot import AnnotationSnapshot
from probe.validation import ValidationReport

DEFAULT_RELATIONS = {
    "F": "enables",
    "P": "involved_in",
    "C": "located_in",
}


class ChangeKind(StrEnum):
    TERM_ACQUIRED = "term_acquired"
    EVIDENCE_UPGRADED = "evidence_upgraded"
    EVIDENCE_CHANGED = "evidence_changed"
    ANNOTATION_REMOVED = "annotation_removed"


class ExclusionReason(StrEnum):
    NOT_ASSERTION = "not_assertion"
    TRUE_PATH_VIOLATION = "true_path_violation"
    UNKNOWN_TERM = "unknown_term"
    OBSOLETE_TERM = "obsolete_term"
    DEPRECATED_TERM = "deprecated_term"


@dataclass(frozen=True, slots=True)
class AnnotationExclusion:
    snapshot_release: str
    annotation: AnnotationRecord
    reason: ExclusionReason
    normalized_term_id: str | None = None


@dataclass(frozen=True, slots=True)
class AnnotationChange:
    sequence_id: str
    target_ids: tuple[str, ...]
    term_id: str
    relation: str
    aspect: str
    kind: ChangeKind
    old_evidence: frozenset[str]
    new_evidence: frozenset[str]
    normalized_from: frozenset[str]
    qualifies: bool


@dataclass(frozen=True, slots=True)
class ComparisonResult:
    changes: tuple[AnnotationChange, ...]
    exclusions: tuple[AnnotationExclusion, ...]
    validation: ValidationReport
    evidence_policy: EvidencePolicy

    @property
    def selected_sequence_ids(self) -> frozenset[str]:
        return frozenset(
            change.sequence_id for change in self.changes if change.qualifies
        )

    @property
    def selected_targets(self) -> frozenset[str]:
        return frozenset(
            target
            for change in self.changes
            if change.qualifies
            for target in change.target_ids
        )

    def __len__(self) -> int:
        return len(self.changes)


@dataclass(slots=True)
class _AssertionState:
    evidence: set[str]
    normalized_from: set[str]


AssertionKey = tuple[str, str, str, str]


def _identity_lookup(
    identities: IdentityMap,
    report: ValidationReport,
) -> tuple[dict[str, str], dict[str, tuple[str, ...]]]:
    subject_to_sequence: dict[str, str] = {}
    targets_by_sequence: dict[str, set[str]] = defaultdict(set)
    for match in identities.matches:
        targets_by_sequence[match.sequence_id].add(match.target_id)
        subjects = {match.target_id, *(alias.identifier for alias in match.aliases)}
        for subject in subjects:
            previous = subject_to_sequence.setdefault(subject, match.sequence_id)
            if previous != match.sequence_id:
                report.error(
                    "AMBIGUOUS_SEQUENCE_ALIAS",
                    f"{subject!r} refers to more than one exact sequence",
                )
    return subject_to_sequence, {
        sequence_id: tuple(sorted(targets))
        for sequence_id, targets in targets_by_sequence.items()
    }


def _relation(annotation: AnnotationRecord) -> str:
    return annotation.relation or DEFAULT_RELATIONS[annotation.aspect]


def _invalid_reason(resolution: TermResolution) -> ExclusionReason:
    if resolution.status is TermStatus.OBSOLETE:
        return ExclusionReason.OBSOLETE_TERM
    if resolution.status is TermStatus.DEPRECATED:
        return ExclusionReason.DEPRECATED_TERM
    return ExclusionReason.UNKNOWN_TERM


def _assertions(
    snapshot: AnnotationSnapshot,
    subject_to_sequence: dict[str, str],
    exclusions: list[AnnotationExclusion],
    report: ValidationReport,
) -> dict[AssertionKey, _AssertionState]:
    usable: list[tuple[AnnotationRecord, str, TermResolution]] = []
    negated_by_subject: dict[tuple[str, str], set[str]] = defaultdict(set)

    for annotation in snapshot.annotations:
        sequence_id = subject_to_sequence.get(annotation.subject_id)
        if sequence_id is None:
            continue
        resolution = snapshot.ontology.resolve(annotation.term_id)
        if not resolution.is_usable:
            reason = _invalid_reason(resolution)
            exclusions.append(
                AnnotationExclusion(snapshot.release, annotation, reason)
            )
            report.warning(
                reason.value.upper(),
                f"excluded {annotation.term_id} from {snapshot.release}",
                source=annotation.source,
                line=annotation.line,
            )
            continue

        usable.append((annotation, sequence_id, resolution))
        if annotation.negated and resolution.canonical_id is not None:
            negated_by_subject[(sequence_id, annotation.aspect)].add(
                resolution.canonical_id
            )

    forbidden: dict[tuple[str, str], frozenset[str]] = {
        key: snapshot.ontology.excluded_by_not(term_ids)
        for key, term_ids in negated_by_subject.items()
    }

    assertions: dict[AssertionKey, _AssertionState] = {}
    for annotation, sequence_id, resolution in usable:
        term_id = resolution.canonical_id
        if term_id is None:
            continue
        if annotation.negated:
            exclusions.append(
                AnnotationExclusion(
                    snapshot.release,
                    annotation,
                    ExclusionReason.NOT_ASSERTION,
                    term_id,
                )
            )
            continue
        if term_id in forbidden.get((sequence_id, annotation.aspect), ()):
            exclusions.append(
                AnnotationExclusion(
                    snapshot.release,
                    annotation,
                    ExclusionReason.TRUE_PATH_VIOLATION,
                    term_id,
                )
            )
            report.warning(
                "TRUE_PATH_VIOLATION",
                f"excluded positive {term_id} because of a NOT ancestor",
                source=annotation.source,
                line=annotation.line,
            )
            continue

        key = (sequence_id, term_id, _relation(annotation), annotation.aspect)
        state = assertions.setdefault(key, _AssertionState(set(), set()))
        state.evidence.add(annotation.evidence)
        if annotation.term_id != term_id:
            state.normalized_from.add(annotation.term_id)
    return assertions


def compare_annotations(
    old: AnnotationSnapshot,
    new: AnnotationSnapshot,
    *,
    identities: IdentityMap,
    evidence_policy: EvidencePolicy | None = None,
    strict: bool = True,
) -> ComparisonResult:
    """Compare direct assertions interpreted through one pinned GO snapshot."""

    policy = evidence_policy or EvidencePolicy.experimental()
    report = ValidationReport()
    report.extend(old.validation)
    report.extend(new.validation)
    exclusions: list[AnnotationExclusion] = []

    if old.ontology is not new.ontology:
        report.error(
            "ONTOLOGY_SNAPSHOT_MISMATCH",
            "old and new annotations must share the same GeneOntology object",
        )
        if strict:
            report.raise_for_errors()
        return ComparisonResult((), (), report, policy)

    subject_to_sequence, targets_by_sequence = _identity_lookup(identities, report)
    before = _assertions(old, subject_to_sequence, exclusions, report)
    after = _assertions(new, subject_to_sequence, exclusions, report)

    changes: list[AnnotationChange] = []
    for key in sorted(before.keys() | after.keys()):
        sequence_id, term_id, relation, aspect = key
        old_state = before.get(key)
        new_state = after.get(key)
        old_evidence = frozenset(old_state.evidence) if old_state else frozenset()
        new_evidence = frozenset(new_state.evidence) if new_state else frozenset()
        normalized_from = frozenset(
            (old_state.normalized_from if old_state else set())
            | (new_state.normalized_from if new_state else set())
        )

        if old_state is None:
            kind = ChangeKind.TERM_ACQUIRED
            qualifies = policy.accepts(new_evidence)
        elif new_state is None:
            kind = ChangeKind.ANNOTATION_REMOVED
            qualifies = False
        elif policy.is_upgrade(old_evidence, new_evidence):
            kind = ChangeKind.EVIDENCE_UPGRADED
            qualifies = True
        elif old_evidence != new_evidence:
            kind = ChangeKind.EVIDENCE_CHANGED
            qualifies = False
        else:
            continue

        changes.append(
            AnnotationChange(
                sequence_id=sequence_id,
                target_ids=targets_by_sequence[sequence_id],
                term_id=term_id,
                relation=relation,
                aspect=aspect,
                kind=kind,
                old_evidence=old_evidence,
                new_evidence=new_evidence,
                normalized_from=normalized_from,
                qualifies=qualifies,
            )
        )

    if strict:
        report.raise_for_errors()
    return ComparisonResult(tuple(changes), tuple(exclusions), report, policy)
