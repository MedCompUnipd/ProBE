"""Compare annotation snapshots over exact sequence identities."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum

from probe.evidence import EvidencePolicy
from probe.identity import IdentityMap
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
    projected_from: frozenset[str]
    qualifies: bool


@dataclass(frozen=True, slots=True)
class ComparisonResult:
    changes: tuple[AnnotationChange, ...]
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
    projected_from: set[str]


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


def _old_assertions(
    snapshot: AnnotationSnapshot,
    subject_to_sequence: dict[str, str],
    report: ValidationReport,
) -> dict[AssertionKey, _AssertionState]:
    assertions: dict[AssertionKey, _AssertionState] = {}
    for annotation in snapshot.annotations:
        sequence_id = subject_to_sequence.get(annotation.subject_id)
        if sequence_id is None:
            continue
        if annotation.negated:
            report.warning(
                "NEGATED_ANNOTATION_IGNORED",
                "negated annotations are retained but not compared yet",
                source=annotation.source,
                line=annotation.line,
            )
            continue
        term_id = snapshot.ontology.resolve_id(annotation.term_id)
        if term_id is None:
            continue
        key = (sequence_id, term_id, _relation(annotation), annotation.aspect)
        state = assertions.setdefault(key, _AssertionState(set(), set()))
        state.evidence.add(annotation.evidence)
    return assertions


def _new_assertions(
    old: AnnotationSnapshot,
    new: AnnotationSnapshot,
    subject_to_sequence: dict[str, str],
    report: ValidationReport,
) -> dict[AssertionKey, _AssertionState]:
    assertions: dict[AssertionKey, _AssertionState] = {}
    for annotation in new.annotations:
        sequence_id = subject_to_sequence.get(annotation.subject_id)
        if sequence_id is None:
            continue
        if annotation.negated:
            report.warning(
                "NEGATED_ANNOTATION_IGNORED",
                "negated annotations are retained but not compared yet",
                source=annotation.source,
                line=annotation.line,
            )
            continue
        projection = old.ontology.project_from(new.ontology, annotation.term_id)
        if not projection.is_mappable:
            report.warning(
                "UNMAPPABLE_NEW_TERM",
                f"{annotation.term_id} cannot be projected into ontology {old.release}",
                source=annotation.source,
                line=annotation.line,
            )
            continue
        for term_id in projection.target_terms:
            key = (sequence_id, term_id, _relation(annotation), annotation.aspect)
            state = assertions.setdefault(key, _AssertionState(set(), set()))
            state.evidence.add(annotation.evidence)
            if term_id != projection.source_term:
                state.projected_from.add(projection.source_term)
    return assertions


def compare_annotations(
    old: AnnotationSnapshot,
    new: AnnotationSnapshot,
    *,
    identities: IdentityMap,
    evidence_policy: EvidencePolicy | None = None,
    strict: bool = True,
) -> ComparisonResult:
    """Return direct annotation changes expressed in the old GO vocabulary."""

    policy = evidence_policy or EvidencePolicy.experimental()
    report = ValidationReport()
    report.extend(old.validation)
    report.extend(new.validation)
    subject_to_sequence, targets_by_sequence = _identity_lookup(identities, report)
    before = _old_assertions(old, subject_to_sequence, report)
    after = _new_assertions(old, new, subject_to_sequence, report)

    changes: list[AnnotationChange] = []
    for key in sorted(before.keys() | after.keys()):
        sequence_id, term_id, relation, aspect = key
        old_state = before.get(key)
        new_state = after.get(key)
        old_evidence = frozenset(old_state.evidence) if old_state else frozenset()
        new_evidence = frozenset(new_state.evidence) if new_state else frozenset()
        projected_from = (
            frozenset(new_state.projected_from) if new_state else frozenset()
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
                projected_from=projected_from,
                qualifies=qualifies,
            )
        )

    if strict:
        report.raise_for_errors()
    return ComparisonResult(tuple(changes), report, policy)
