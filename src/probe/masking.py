"""Construction of Q, closure provenance, masks, and final evaluable truth."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from types import MappingProxyType

from probe.comparison import ComparisonResult
from probe.knowledge import DirectTermState
from probe.ontology import GO_ROOT_BY_NAMESPACE
from probe.ontology_validation import (
    OntologyPreflightReport,
    ReachabilityFinding,
)
from probe.parsing.owl import OntologyContext
from probe.relations import (
    EdgeDispositionKind,
    GOAspect,
    PolicyIdentity,
)
from probe.truth import (
    ConfirmationMaskPolicy,
    DirectTruthSet,
    DirectTruthTerm,
    NeutralTruthPolicy,
    PositiveAssertionPolicy,
    SnapshotOrigin,
    TruthSelectionProfile,
    direct_term_from_state,
    evidence_policy_fingerprint,
)

ASPECT_NAMESPACES = {
    GOAspect.MF: "molecular_function",
    GOAspect.BP: "biological_process",
    GOAspect.CC: "cellular_component",
}


def _go_id(iri: str) -> str | None:
    prefix = "http://purl.obolibrary.org/obo/GO_"
    return f"GO:{iri.removeprefix(prefix)}" if iri.startswith(prefix) else None


@dataclass(frozen=True, slots=True)
class EvaluationUniversePolicy:
    identity: PolicyIdentity
    admit_part_of_only_root_paths: bool

    @classmethod
    def is_a_rooted(cls) -> EvaluationUniversePolicy:
        config = {"admit_part_of_only_root_paths": False, "exclude_roots": True}
        return cls(
            PolicyIdentity.from_configuration("is_a_rooted_universe", "1", config),
            False,
        )

    @classmethod
    def safe_root_paths(cls) -> EvaluationUniversePolicy:
        config = {"admit_part_of_only_root_paths": True, "exclude_roots": True}
        return cls(
            PolicyIdentity.from_configuration("safe_root_path_universe", "1", config),
            True,
        )


@dataclass(frozen=True, slots=True)
class EvaluationUniverse:
    universe_id: str
    aspect: GOAspect
    terms: frozenset[str]
    root_term_id: str
    ontology_fingerprint: str
    universe_policy_fingerprint: str
    preflight_report_id: str


@dataclass(frozen=True, slots=True)
class RelationStep:
    source: str
    relation_iri: str
    target: str


@dataclass(frozen=True, slots=True)
class ClosureEntry:
    direct_term_id: str
    propagated_term_id: str
    minimum_distance: int
    relation_iris: frozenset[str]
    witness_path: tuple[RelationStep, ...]
    entry_id: str


@dataclass(frozen=True, slots=True)
class ClosureResult:
    closure_id: str
    terms: frozenset[str]
    entries: tuple[ClosureEntry, ...]
    sources_by_term: tuple[tuple[str, tuple[str, ...]], ...]
    ontology_fingerprint: str
    propagation_policy_fingerprint: str


@dataclass(frozen=True, slots=True)
class PropagatedTruthSet:
    direct_terms: frozenset[str]
    terms: frozenset[str]
    closure_entries: tuple[ClosureEntry, ...]


@dataclass(frozen=True, slots=True)
class PriorKnownMask:
    direct_terms: frozenset[str]
    terms: frozenset[str]
    closure_entries: tuple[ClosureEntry, ...]
    policy_fingerprint: str


@dataclass(frozen=True, slots=True)
class NeutralTruthMask:
    direct_terms: frozenset[str]
    raw_closure: frozenset[str]
    terms: frozenset[str]
    closure_entries: tuple[ClosureEntry, ...]
    policy_fingerprint: str


class MaskReason(StrEnum):
    ROOT = "root"
    OUTSIDE_Q = "outside_q"
    PRIOR_KNOWN = "prior_known"
    NEUTRAL_T1 = "neutral_t1"


@dataclass(frozen=True, slots=True)
class MaskDecision:
    term_id: str
    primary_reason: MaskReason | None
    all_reasons: frozenset[MaskReason]
    source_direct_terms: tuple[str, ...]
    inclusion: bool


class TargetAspectStatus(StrEnum):
    EVALUABLE = "evaluable"
    EXCLUDED_NO_SCORED_DIRECT_TRUTH = "excluded_no_scored_direct_truth"
    EXCLUDED_EMPTY_EVALUABLE_TRUTH = "excluded_empty_evaluable_truth"


@dataclass(frozen=True, slots=True)
class TargetAspectTruth:
    sequence_id: str
    target_ids: tuple[str, ...]
    aspect: GOAspect
    d_score: DirectTruthSet
    g_candidate: PropagatedTruthSet
    d_neutral1: DirectTruthSet
    k0: PriorKnownMask
    x1: NeutralTruthMask
    evaluation_universe_id: str
    m: frozenset[str]
    truth: frozenset[str]
    decisions: tuple[MaskDecision, ...]
    preflight_report_id: str
    ontology_fingerprint: str
    status: TargetAspectStatus


def build_evaluation_universe(
    ontology: OntologyContext,
    *,
    aspect: GOAspect,
    approved_preflight: OntologyPreflightReport,
    policy: EvaluationUniversePolicy,
) -> EvaluationUniverse:
    if approved_preflight.ontology_fingerprint != ontology.fingerprint.value:
        raise ValueError("ontology/preflight fingerprint mismatch")
    admitted = {ReachabilityFinding.VALID_IS_A_ROOT_PATH}
    if policy.admit_part_of_only_root_paths:
        admitted.add(ReachabilityFinding.NO_IS_A_ROOT_PATH_BUT_SAFE_PART_OF_PATH)
    terms = frozenset(
        item.term_id
        for item in approved_preflight.reachability
        if item.aspect is aspect
        and ReachabilityFinding.ROOT_TERM not in item.findings
        and bool(item.findings & admitted)
    )
    root = GO_ROOT_BY_NAMESPACE[ASPECT_NAMESPACES[aspect]]
    payload = json.dumps(
        [
            aspect.value,
            sorted(terms),
            ontology.fingerprint.value,
            policy.identity.fingerprint,
        ],
        separators=(",", ":"),
    )
    return EvaluationUniverse(
        hashlib.sha256(payload.encode()).hexdigest(),
        aspect,
        terms,
        root,
        ontology.fingerprint.value,
        policy.identity.fingerprint,
        approved_preflight.report_id,
    )


def build_evaluation_universes(
    ontology: OntologyContext,
    *,
    approved_preflight: OntologyPreflightReport,
    policy: EvaluationUniversePolicy,
) -> Mapping[GOAspect, EvaluationUniverse]:
    return MappingProxyType(
        {
            aspect: build_evaluation_universe(
                ontology,
                aspect=aspect,
                approved_preflight=approved_preflight,
                policy=policy,
            )
            for aspect in GOAspect
        }
    )


def _propagating_adjacency(
    ontology: OntologyContext, aspect: GOAspect
) -> dict[str, tuple[tuple[str, str], ...]]:
    adjacency: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for edge in ontology.edges:
        if edge.disposition is not EdgeDispositionKind.PROPAGATING:
            continue
        if edge.child_aspect is not aspect or edge.parent_aspect is not aspect:
            continue
        child, parent = _go_id(edge.child_iri), _go_id(edge.parent_iri)
        if child and parent:
            adjacency[child].add((edge.relation_iri, parent))
    return {key: tuple(sorted(value)) for key, value in adjacency.items()}


def inclusive_ancestor_closure(
    direct_terms: Iterable[DirectTruthTerm],
    *,
    ontology: OntologyContext,
) -> ClosureResult:
    canonical_records: list[DirectTruthTerm] = []
    for record in direct_terms:
        term = ontology.ontology.term(record.term_id)
        if term is None or not term.is_active:
            raise ValueError(f"invalid or unmappable GO term: {record.term_id}")
        if term.namespace != ASPECT_NAMESPACES[record.aspect]:
            raise ValueError(
                f"GO term {record.term_id} does not belong to aspect "
                f"{record.aspect.value}"
            )
        if term.identifier != record.term_id:
            record = replace(
                record,
                term_id=term.identifier,
                normalized_from=record.normalized_from | {record.term_id},
            )
        canonical_records.append(record)
    records = tuple(
        sorted(canonical_records, key=lambda item: (item.term_id, item.sequence_id))
    )
    if records and len({item.aspect for item in records}) != 1:
        raise ValueError("one closure cannot mix GO aspects")
    aspect = records[0].aspect if records else GOAspect.BP
    adjacency = _propagating_adjacency(ontology, aspect)
    entries: list[ClosureEntry] = []
    sources: dict[str, set[str]] = defaultdict(set)
    for record in records:
        queue = deque([record.term_id])
        best: dict[str, int] = {record.term_id: 0}
        witnesses: dict[str, tuple[RelationStep, ...]] = {record.term_id: ()}
        encountered_relations: dict[str, set[str]] = {record.term_id: set()}
        while queue:
            node = queue.popleft()
            distance = best[node]
            for relation_iri, parent in adjacency.get(node, ()):
                step = RelationStep(node, relation_iri, parent)
                candidate_witness = (*witnesses[node], step)
                candidate_relations = encountered_relations[node] | {relation_iri}
                candidate_distance = distance + 1
                previous = best.get(parent)
                if previous is None or candidate_distance < previous:
                    best[parent] = candidate_distance
                    witnesses[parent] = candidate_witness
                    encountered_relations[parent] = set(candidate_relations)
                    queue.append(parent)
                elif candidate_distance == previous:
                    changed = False
                    candidate_key = tuple(
                        (item.source, item.relation_iri, item.target)
                        for item in candidate_witness
                    )
                    witness_key = tuple(
                        (item.source, item.relation_iri, item.target)
                        for item in witnesses[parent]
                    )
                    if candidate_key < witness_key:
                        witnesses[parent] = candidate_witness
                        changed = True
                    combined = encountered_relations[parent] | candidate_relations
                    if combined != encountered_relations[parent]:
                        encountered_relations[parent] = combined
                        changed = True
                    if changed:
                        queue.append(parent)
        for propagated in sorted(best):
            witness = witnesses[propagated]
            relation_iris = frozenset(encountered_relations[propagated])
            entry_payload = json.dumps(
                [
                    record.term_id,
                    propagated,
                    best[propagated],
                    [(s.source, s.relation_iri, s.target) for s in witness],
                    ontology.fingerprint.value,
                    ontology.relation_policy.identity.fingerprint,
                ],
                separators=(",", ":"),
            )
            entries.append(
                ClosureEntry(
                    record.term_id,
                    propagated,
                    best[propagated],
                    relation_iris,
                    witness,
                    hashlib.sha256(entry_payload.encode()).hexdigest(),
                )
            )
            sources[propagated].add(record.term_id)
    entries_tuple = tuple(
        sorted(entries, key=lambda item: (item.propagated_term_id, item.direct_term_id))
    )
    terms = frozenset(sources)
    source_tuple = tuple(
        (term, tuple(sorted(values))) for term, values in sorted(sources.items())
    )
    payload = json.dumps(
        [
            sorted(terms),
            [
                (entry.direct_term_id, entry.propagated_term_id)
                for entry in entries_tuple
            ],
            ontology.fingerprint.value,
            ontology.relation_policy.identity.fingerprint,
        ],
        separators=(",", ":"),
    )
    return ClosureResult(
        hashlib.sha256(payload.encode()).hexdigest(),
        terms,
        entries_tuple,
        source_tuple,
        ontology.fingerprint.value,
        ontology.relation_policy.identity.fingerprint,
    )


def _direct_set(
    sequence_id: str,
    aspect: GOAspect,
    fingerprint: str,
    records: Iterable[DirectTruthTerm],
) -> DirectTruthSet:
    selected = tuple(sorted(records, key=lambda item: item.term_id))
    return DirectTruthSet(
        sequence_id,
        aspect,
        fingerprint,
        frozenset(item.term_id for item in selected),
        selected,
    )


def _state_valid_for_aspect(
    state: DirectTermState, ontology: OntologyContext, aspect: GOAspect
) -> bool:
    term = ontology.ontology.term(state.term_id)
    return bool(term and term.is_active and term.namespace == ASPECT_NAMESPACES[aspect])


def construct_truth_and_masks(
    comparison: ComparisonResult,
    *,
    ontology: OntologyContext,
    truth_profile: TruthSelectionProfile,
    prior_policy: PositiveAssertionPolicy,
    neutral_policy: NeutralTruthPolicy,
    confirmation_mask_policy: ConfirmationMaskPolicy,
    approved_preflight: OntologyPreflightReport,
    universes: Mapping[GOAspect, EvaluationUniverse],
) -> tuple[TargetAspectTruth, ...]:
    if comparison.ontology_fingerprint != ontology.fingerprint.value:
        raise ValueError("comparison/ontology fingerprint mismatch")
    if truth_profile.evidence_policy_fingerprint != evidence_policy_fingerprint(
        comparison.evidence_policy
    ):
        raise ValueError(
            "truth profile is incompatible with comparison evidence policy"
        )
    if (
        truth_profile.confirmation_mask_policy_fingerprint
        != confirmation_mask_policy.identity.fingerprint
    ):
        raise ValueError("truth profile is incompatible with confirmation mask policy")
    if approved_preflight.ontology_fingerprint != ontology.fingerprint.value:
        raise ValueError("preflight/ontology fingerprint mismatch")

    new_by_key = {state.key: state for state in comparison.new_assertions}
    targets_by_sequence: dict[str, set[str]] = defaultdict(set)
    for event in comparison.events:
        targets_by_sequence[event.sequence_id].update(event.target_ids)
    for prior in comparison.prior_knowledge:
        targets_by_sequence[prior.sequence_id].update(prior.target_ids)
    sequence_ids = sorted(
        {
            state.sequence_id
            for state in (*comparison.old_assertions, *comparison.new_assertions)
        }
        | set(targets_by_sequence)
    )
    results: list[TargetAspectTruth] = []
    for sequence_id in sequence_ids:
        for aspect in GOAspect:
            universe = universes[aspect]
            if universe.ontology_fingerprint != ontology.fingerprint.value:
                raise ValueError("evaluation universe/ontology fingerprint mismatch")
            selected_events = {
                event.term_id: event
                for event in comparison.events
                if event.sequence_id == sequence_id
                and event.aspect == aspect.value
                and event.event_type in truth_profile.event_types
            }
            scored_records = []
            for term_id, event in sorted(selected_events.items()):
                state = new_by_key.get((sequence_id, aspect.value, term_id))
                if state and _state_valid_for_aspect(state, ontology, aspect):
                    scored_records.append(
                        direct_term_from_state(
                            state,
                            origin=SnapshotOrigin.T1,
                            selecting_event=event.event_type,
                        )
                    )
            d_score = _direct_set(
                sequence_id, aspect, truth_profile.identity.fingerprint, scored_records
            )
            scored_closure = inclusive_ancestor_closure(
                d_score.records, ontology=ontology
            )
            g_candidate = PropagatedTruthSet(
                d_score.terms, scored_closure.terms, scored_closure.entries
            )

            prior_records = tuple(
                direct_term_from_state(state, origin=SnapshotOrigin.T0)
                for state in comparison.old_assertions
                if state.sequence_id == sequence_id
                and state.aspect == aspect.value
                and _state_valid_for_aspect(state, ontology, aspect)
                and prior_policy.accepts(state.evidence_codes)
            )
            prior_closure = inclusive_ancestor_closure(prior_records, ontology=ontology)
            k0_terms = prior_closure.terms
            if confirmation_mask_policy.mode == "unmask_upgraded_direct_only":
                k0_terms = k0_terms - d_score.terms
            k0 = PriorKnownMask(
                frozenset(item.term_id for item in prior_records),
                k0_terms,
                prior_closure.entries,
                prior_policy.identity.fingerprint,
            )

            neutral_records = tuple(
                direct_term_from_state(state, origin=SnapshotOrigin.T1)
                for state in comparison.new_assertions
                if state.sequence_id == sequence_id
                and state.aspect == aspect.value
                and state.term_id not in d_score.terms
                and _state_valid_for_aspect(state, ontology, aspect)
                and neutral_policy.accepts(state.evidence_codes)
            )
            d_neutral1 = _direct_set(
                sequence_id,
                aspect,
                neutral_policy.identity.fingerprint,
                neutral_records,
            )
            neutral_closure = inclusive_ancestor_closure(
                d_neutral1.records, ontology=ontology
            )
            x1_terms = neutral_closure.terms - scored_closure.terms
            x1 = NeutralTruthMask(
                d_neutral1.terms,
                neutral_closure.terms,
                x1_terms,
                neutral_closure.entries,
                neutral_policy.identity.fingerprint,
            )
            m = universe.terms - k0.terms - x1.terms
            truth = g_candidate.terms & m
            sources = dict(scored_closure.sources_by_term)
            decisions = []
            for term_id in sorted(g_candidate.terms):
                reasons: set[MaskReason] = set()
                if term_id == universe.root_term_id:
                    reasons.add(MaskReason.ROOT)
                elif term_id not in universe.terms:
                    reasons.add(MaskReason.OUTSIDE_Q)
                if term_id in k0.terms:
                    reasons.add(MaskReason.PRIOR_KNOWN)
                if term_id in x1.terms:
                    reasons.add(MaskReason.NEUTRAL_T1)
                if MaskReason.ROOT in reasons:
                    primary = MaskReason.ROOT
                elif MaskReason.OUTSIDE_Q in reasons:
                    primary = MaskReason.OUTSIDE_Q
                elif MaskReason.PRIOR_KNOWN in reasons:
                    primary = MaskReason.PRIOR_KNOWN
                elif MaskReason.NEUTRAL_T1 in reasons:
                    primary = MaskReason.NEUTRAL_T1
                else:
                    primary = None
                decisions.append(
                    MaskDecision(
                        term_id,
                        primary,
                        frozenset(reasons),
                        sources.get(term_id, ()),
                        term_id in truth,
                    )
                )
            if not d_score.terms:
                status = TargetAspectStatus.EXCLUDED_NO_SCORED_DIRECT_TRUTH
            elif not truth:
                status = TargetAspectStatus.EXCLUDED_EMPTY_EVALUABLE_TRUTH
            else:
                status = TargetAspectStatus.EVALUABLE
            results.append(
                TargetAspectTruth(
                    sequence_id,
                    tuple(sorted(targets_by_sequence.get(sequence_id, ()))),
                    aspect,
                    d_score,
                    g_candidate,
                    d_neutral1,
                    k0,
                    x1,
                    universe.universe_id,
                    m,
                    truth,
                    tuple(decisions),
                    approved_preflight.report_id,
                    ontology.fingerprint.value,
                    status,
                )
            )
    return tuple(results)
