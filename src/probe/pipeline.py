"""Notebook-first orchestration of synchronized t0/t1 benchmark inputs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from probe.comparison import ComparisonResult, compare_annotations
from probe.evidence import EvidencePolicy
from probe.identity import (
    IdentityMap,
    SequenceDataset,
    SequenceIndex,
    SequenceMatch,
)
from probe.knowledge import AssertionContext, EventSuperclass, EventType
from probe.masking import (
    EvaluationUniverse,
    EvaluationUniversePolicy,
    TargetAspectTruth,
    build_evaluation_universes,
    construct_truth_and_masks,
)
from probe.ontology_validation import OntologyPreflightReport, preflight_ontology
from probe.parsing.owl import OntologyContext, OwlLoader
from probe.records import AnnotationRecord
from probe.relations import GOAspect
from probe.snapshot import AnnotationSnapshot
from probe.truth import (
    ConfirmationMaskPolicy,
    NeutralTruthPolicy,
    PositiveAssertionPolicy,
    TruthSelectionProfile,
)


@dataclass(frozen=True, slots=True)
class OntologyInput:
    path: Path
    expected_sha256: str
    source_url: str
    retrieval_date: str


@dataclass(frozen=True, slots=True)
class ReleaseInput:
    release: str
    uniprot_fasta: Path
    goa: Path


@dataclass(frozen=True, slots=True)
class ReleaseMatch:
    release: str
    identities: IdentityMap
    annotations: AnnotationSnapshot


@dataclass(frozen=True, slots=True)
class DirectGroundTruthCandidate:
    """A qualifying direct event, not yet the final masked ground truth."""

    sequence_id: str
    target_ids: tuple[str, ...]
    aspect: str
    term_id: str
    event_type: EventType
    event_superclass: EventSuperclass
    old_evidence: frozenset[str]
    new_evidence: frozenset[str]
    old_contexts: frozenset[AssertionContext]
    new_contexts: frozenset[AssertionContext]
    normalized_from: frozenset[str]
    source_assertions: tuple[AnnotationRecord, ...]


class GroundTruthNotFinalError(RuntimeError):
    """Raised when direct events are requested as final evaluable truth."""


@dataclass(frozen=True, slots=True)
class TruthMaskConfiguration:
    """All explicit scientific policies required to construct final truth."""

    truth_profile: TruthSelectionProfile
    prior_policy: PositiveAssertionPolicy
    neutral_policy: NeutralTruthPolicy
    confirmation_mask_policy: ConfirmationMaskPolicy
    universe_policy: EvaluationUniversePolicy


@dataclass(frozen=True, slots=True)
class BenchmarkPipelineResult:
    targets: SequenceDataset
    ontology: OntologyContext
    preflight: OntologyPreflightReport
    t0: ReleaseMatch
    t1: ReleaseMatch
    identities: IdentityMap
    comparison: ComparisonResult
    evidence_policy: EvidencePolicy
    direct_candidates: tuple[DirectGroundTruthCandidate, ...]
    evaluation_universes: Mapping[GOAspect, EvaluationUniverse] | None = None
    final_ground_truth: tuple[TargetAspectTruth, ...] | None = None

    def require_final_ground_truth(self) -> tuple[TargetAspectTruth, ...]:
        if self.final_ground_truth is None:
            raise GroundTruthNotFinalError(
                "direct candidates are not final ground truth; supply explicit "
                "truth/mask policies to construct Q, K0, X1, M, and T"
            )
        return self.final_ground_truth


def _merge_identities(
    targets: SequenceDataset,
    t0: IdentityMap,
    t1: IdentityMap,
) -> IdentityMap:
    aliases_by_match: dict[tuple[str, str], set] = {}
    for identity_map in (t0, t1):
        for match in identity_map.matches:
            aliases_by_match.setdefault(
                (match.target_id, match.sequence_id), set()
            ).update(match.aliases)
    matches = tuple(
        SequenceMatch(
            target_id=target_id,
            sequence_id=sequence_id,
            aliases=tuple(sorted(aliases)),
        )
        for (target_id, sequence_id), aliases in sorted(aliases_by_match.items())
    )
    matched_targets = {match.target_id for match in matches}
    unmatched = tuple(
        sorted(
            record.identifier
            for record in targets.records
            if record.identifier not in matched_targets
        )
    )
    return IdentityMap(matches, unmatched)


def _match_release(
    release: ReleaseInput,
    *,
    targets: SequenceDataset,
    ontology: OntologyContext,
) -> ReleaseMatch:
    sequence_index = SequenceIndex.from_fasta(
        release.uniprot_fasta,
        source_name=release.release,
    )
    identities = sequence_index.match(targets)
    annotations = AnnotationSnapshot.read(
        release=release.release,
        annotations=release.goa,
        ontology=ontology.ontology,
        subjects=identities.subject_ids,
    )
    return ReleaseMatch(release.release, identities, annotations)


def _direct_candidates(
    comparison: ComparisonResult,
) -> tuple[DirectGroundTruthCandidate, ...]:
    new_states = {state.key: state for state in comparison.new_assertions}
    candidates: list[DirectGroundTruthCandidate] = []
    for event in comparison.events:
        if not event.qualifies_for_benchmark:
            continue
        state = new_states[event.key]
        candidates.append(
            DirectGroundTruthCandidate(
                sequence_id=event.sequence_id,
                target_ids=event.target_ids,
                aspect=event.aspect,
                term_id=event.term_id,
                event_type=event.event_type,
                event_superclass=event.event_superclass,
                old_evidence=event.old_evidence,
                new_evidence=event.new_evidence,
                old_contexts=event.old_contexts,
                new_contexts=event.new_contexts,
                normalized_from=state.normalized_from,
                source_assertions=state.source_assertions,
            )
        )
    return tuple(candidates)


def run_benchmark_pipeline(
    *,
    targets_fasta: str | Path,
    ontology: OntologyInput,
    t0: ReleaseInput,
    t1: ReleaseInput,
    evidence_policy: EvidencePolicy | None = None,
    truth_mask: TruthMaskConfiguration | None = None,
) -> BenchmarkPipelineResult:
    """Match targets independently at t0/t1 and compare their direct GO states.

    Inputs are expected to be outputs of the release preprocessor. Without a
    ``truth_mask`` configuration the function deliberately stops at qualifying
    direct candidates. With all policies supplied explicitly, it also constructs
    the distinct Q, D_score, G_candidate, K0, D_neutral1, X1, M, and T objects.
    """

    if t0.release == t1.release:
        raise ValueError("t0 and t1 must have distinct release identifiers")
    policy = evidence_policy or EvidencePolicy.experimental_strict()
    context = OwlLoader().load_context(
        ontology.path,
        expected_sha256=ontology.expected_sha256,
        source_url=ontology.source_url,
        retrieval_date=ontology.retrieval_date,
    )
    preflight = preflight_ontology(context)
    targets = SequenceDataset.read(targets_fasta)
    matched_t0 = _match_release(t0, targets=targets, ontology=context)
    matched_t1 = _match_release(t1, targets=targets, ontology=context)
    identities = _merge_identities(
        targets,
        matched_t0.identities,
        matched_t1.identities,
    )
    comparison = compare_annotations(
        matched_t0.annotations,
        matched_t1.annotations,
        identities=identities,
        evidence_policy=policy,
        ontology_fingerprint=context.fingerprint.value,
    )
    universes: Mapping[GOAspect, EvaluationUniverse] | None = None
    final_ground_truth: tuple[TargetAspectTruth, ...] | None = None
    if truth_mask is not None:
        universes = build_evaluation_universes(
            context,
            approved_preflight=preflight,
            policy=truth_mask.universe_policy,
        )
        final_ground_truth = construct_truth_and_masks(
            comparison,
            ontology=context,
            truth_profile=truth_mask.truth_profile,
            prior_policy=truth_mask.prior_policy,
            neutral_policy=truth_mask.neutral_policy,
            confirmation_mask_policy=truth_mask.confirmation_mask_policy,
            approved_preflight=preflight,
            universes=universes,
        )
    return BenchmarkPipelineResult(
        targets=targets,
        ontology=context,
        preflight=preflight,
        t0=matched_t0,
        t1=matched_t1,
        identities=identities,
        comparison=comparison,
        evidence_policy=policy,
        direct_candidates=_direct_candidates(comparison),
        evaluation_universes=universes,
        final_ground_truth=final_ground_truth,
    )
