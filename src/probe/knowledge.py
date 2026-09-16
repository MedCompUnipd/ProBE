"""Pure classification of canonical direct annotation events."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from probe.evidence import EvidencePolicy
from probe.ontology import ANNOTATION_PROPAGATION_RELATIONS, GeneOntology
from probe.records import AnnotationRecord

CanonicalAssertionKey = tuple[str, str, str]


@dataclass(frozen=True, slots=True)
class AssertionContext:
    """Assertion fields retained outside the biological membership key."""

    relation: str
    qualifiers: frozenset[str]
    references: tuple[str, ...]
    with_from: tuple[str, ...]
    assigned_by: str
    annotation_date: str
    extensions: tuple[str, ...]
    gene_product_form_id: str

    @classmethod
    def from_annotation(
        cls,
        annotation: AnnotationRecord,
        *,
        relation: str,
    ) -> AssertionContext:
        return cls(
            relation=relation,
            qualifiers=frozenset({"NOT"} if annotation.negated else ()),
            references=annotation.references,
            with_from=annotation.with_from,
            assigned_by=annotation.assigned_by,
            annotation_date=annotation.date,
            extensions=annotation.extensions,
            gene_product_form_id=annotation.gene_product_form_id,
        )


@dataclass(frozen=True, slots=True)
class DirectTermState:
    """All direct positive assertions for one canonical biological key."""

    sequence_id: str
    aspect: str
    term_id: str
    evidence_codes: frozenset[str]
    contexts: frozenset[AssertionContext]
    normalized_from: frozenset[str]
    source_assertions: tuple[AnnotationRecord, ...]

    @property
    def key(self) -> CanonicalAssertionKey:
        return self.sequence_id, self.aspect, self.term_id


class PriorKnowledgeState(StrEnum):
    GLOBAL_NONE = "global_none"
    ASPECT_NONE = "aspect_none"
    ASPECT_PRESENT = "aspect_present"


@dataclass(frozen=True, slots=True)
class PriorKnowledge:
    sequence_id: str
    target_ids: tuple[str, ...]
    aspect: str
    state: PriorKnowledgeState


class EventType(StrEnum):
    EVIDENCE_UPGRADE = "evidence_upgrade"
    REDUNDANT_ANCESTOR = "redundant_ancestor"
    SPECIFICITY_REFINEMENT = "specificity_refinement"
    BRANCH_ACQUISITION = "branch_acquisition"


class EventSuperclass(StrEnum):
    NEW_KNOWLEDGE = "new_knowledge"
    KNOWLEDGE_REFINEMENT = "knowledge_refinement"
    EVIDENCE_CONFIRMATION = "evidence_confirmation"
    REDUNDANT_OR_NON_EVALUABLE = "redundant_or_non_evaluable"


@dataclass(frozen=True, slots=True)
class DirectAnnotationEvent:
    sequence_id: str
    target_ids: tuple[str, ...]
    aspect: str
    term_id: str
    prior_knowledge_state: PriorKnowledgeState
    event_type: EventType
    event_superclass: EventSuperclass
    old_evidence: frozenset[str]
    new_evidence: frozenset[str]
    old_contexts: frozenset[AssertionContext]
    new_contexts: frozenset[AssertionContext]
    qualifies_for_benchmark: bool

    @property
    def key(self) -> CanonicalAssertionKey:
        return self.sequence_id, self.aspect, self.term_id


def determine_prior_knowledge(
    before: Mapping[CanonicalAssertionKey, DirectTermState],
    *,
    targets_by_sequence: Mapping[str, tuple[str, ...]],
    evidence_policy: EvidencePolicy,
    aspects: tuple[str, ...] = ("F", "P", "C"),
) -> tuple[PriorKnowledge, ...]:
    """Return the t0 profile-accepted state for every matched sequence/aspect."""

    accepted_aspects: dict[str, set[str]] = defaultdict(set)
    for (sequence_id, aspect, _term_id), state in before.items():
        if evidence_policy.accepts(state.evidence_codes):
            accepted_aspects[sequence_id].add(aspect)

    states: list[PriorKnowledge] = []
    for sequence_id in sorted(targets_by_sequence):
        present = accepted_aspects.get(sequence_id, set())
        for aspect in aspects:
            if aspect in present:
                state = PriorKnowledgeState.ASPECT_PRESENT
            elif present:
                state = PriorKnowledgeState.ASPECT_NONE
            else:
                state = PriorKnowledgeState.GLOBAL_NONE
            states.append(
                PriorKnowledge(
                    sequence_id=sequence_id,
                    target_ids=targets_by_sequence[sequence_id],
                    aspect=aspect,
                    state=state,
                )
            )
    return tuple(states)


def classify_direct_events(
    before: Mapping[CanonicalAssertionKey, DirectTermState],
    after: Mapping[CanonicalAssertionKey, DirectTermState],
    *,
    ontology: GeneOntology,
    targets_by_sequence: Mapping[str, tuple[str, ...]],
    evidence_policy: EvidencePolicy,
) -> tuple[DirectAnnotationEvent, ...]:
    """Classify accepted t1 direct terms using the conservative GO closure."""

    accepted_terms: dict[tuple[str, str], set[str]] = defaultdict(set)
    for (sequence_id, aspect, term_id), state in before.items():
        if evidence_policy.accepts(state.evidence_codes):
            accepted_terms[(sequence_id, aspect)].add(term_id)
    prior_knowledge = {
        (item.sequence_id, item.aspect): item.state
        for item in determine_prior_knowledge(
            before,
            targets_by_sequence=targets_by_sequence,
            evidence_policy=evidence_policy,
        )
    }

    events: list[DirectAnnotationEvent] = []
    for key in sorted(after):
        sequence_id, aspect, term_id = key
        new_state = after[key]
        if not evidence_policy.accepts(new_state.evidence_codes):
            continue

        old_state = before.get(key)
        if old_state is not None and evidence_policy.accepts(old_state.evidence_codes):
            continue

        prior_state = prior_knowledge.get(
            (sequence_id, aspect),
            PriorKnowledgeState.GLOBAL_NONE,
        )
        if old_state is not None:
            event_type = EventType.EVIDENCE_UPGRADE
            event_superclass = EventSuperclass.EVIDENCE_CONFIRMATION
            qualifies = True
        else:
            prior_terms = accepted_terms.get((sequence_id, aspect), set())
            prior_closure = ontology.propagate(
                prior_terms,
                relations=ANNOTATION_PROPAGATION_RELATIONS,
            )
            if term_id in prior_closure:
                event_type = EventType.REDUNDANT_ANCESTOR
                event_superclass = EventSuperclass.REDUNDANT_OR_NON_EVALUABLE
                qualifies = False
            else:
                ancestors = ontology.ancestors(
                    term_id,
                    relations=ANNOTATION_PROPAGATION_RELATIONS,
                )
                if prior_terms.intersection(ancestors):
                    event_type = EventType.SPECIFICITY_REFINEMENT
                    event_superclass = EventSuperclass.KNOWLEDGE_REFINEMENT
                else:
                    event_type = EventType.BRANCH_ACQUISITION
                    event_superclass = EventSuperclass.NEW_KNOWLEDGE
                qualifies = True

        events.append(
            DirectAnnotationEvent(
                sequence_id=sequence_id,
                target_ids=targets_by_sequence.get(sequence_id, ()),
                aspect=aspect,
                term_id=term_id,
                prior_knowledge_state=prior_state,
                event_type=event_type,
                event_superclass=event_superclass,
                old_evidence=(old_state.evidence_codes if old_state else frozenset()),
                new_evidence=new_state.evidence_codes,
                old_contexts=(old_state.contexts if old_state else frozenset()),
                new_contexts=new_state.contexts,
                qualifies_for_benchmark=qualifies,
            )
        )
    return tuple(events)
