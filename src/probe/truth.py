"""Explicit policies and immutable records for direct benchmark truth."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from probe.evidence import EvidencePolicy, EvidenceTier
from probe.knowledge import DirectTermState, EventType
from probe.records import AnnotationRecord
from probe.relations import GOAspect, PolicyIdentity


class SnapshotOrigin(StrEnum):
    T0 = "t0"
    T1 = "t1"


def evidence_policy_fingerprint(policy: EvidencePolicy) -> str:
    """Return a stable identity for the evidence policy used during comparison."""

    value = json.dumps(
        {
            "name": policy.name,
            "accepted_categories": sorted(
                item.value for item in policy.accepted_categories
            ),
            "accepted_tiers": sorted(int(item) for item in policy.accepted_tiers),
            "upgrades": sorted(
                (left.value, right.value) for left, right in policy.upgrades
            ),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class PositiveAssertionPolicy:
    identity: PolicyIdentity
    accepted_tiers: frozenset[EvidenceTier]
    excluded_codes: frozenset[str]
    include_iea_only: bool

    @classmethod
    def all_usable_positive_t0(cls) -> PositiveAssertionPolicy:
        tiers = frozenset(EvidenceTier)
        config = {
            "accepted_tiers": sorted(int(tier) for tier in tiers),
            "excluded_codes": ["NAS", "ND"],
            "include_iea_only": True,
        }
        return cls(
            PolicyIdentity.from_configuration("all_usable_positive_t0", "1", config),
            tiers,
            frozenset({"NAS", "ND"}),
            True,
        )

    def accepts(self, evidence_codes: Iterable[str]) -> bool:
        policy = EvidencePolicy(name="positive_assertion_policy")
        usable = {
            code
            for code in evidence_codes
            if code not in self.excluded_codes
            and (tier := policy.tier(code)) is not None
            and tier in self.accepted_tiers
        }
        return bool(usable) and (self.include_iea_only or usable != {"IEA"})


@dataclass(frozen=True, slots=True)
class ConfirmationMaskPolicy:
    identity: PolicyIdentity
    mode: str

    @classmethod
    def main_knowledge_gain(cls) -> ConfirmationMaskPolicy:
        mode = "retain_full_k0"
        return cls(
            PolicyIdentity.from_configuration(
                "main_knowledge_gain_mask", "1", {"mode": mode}
            ),
            mode,
        )

    @classmethod
    def unmask_upgraded_direct_only(cls) -> ConfirmationMaskPolicy:
        mode = "unmask_upgraded_direct_only"
        return cls(
            PolicyIdentity.from_configuration(
                "evidence_confirmation_mask", "1", {"mode": mode}
            ),
            mode,
        )


@dataclass(frozen=True, slots=True)
class TruthSelectionProfile:
    identity: PolicyIdentity
    event_types: frozenset[EventType]
    evidence_policy_fingerprint: str
    confirmation_mask_policy_fingerprint: str

    @classmethod
    def _create(
        cls,
        name: str,
        event_types: frozenset[EventType],
        evidence_policy: EvidencePolicy,
        confirmation_policy: ConfirmationMaskPolicy,
    ) -> TruthSelectionProfile:
        evidence_fingerprint = evidence_policy_fingerprint(evidence_policy)
        config = {
            "event_types": sorted(item.value for item in event_types),
            "evidence_policy_fingerprint": evidence_fingerprint,
            "confirmation_mask_policy_fingerprint": (
                confirmation_policy.identity.fingerprint
            ),
        }
        return cls(
            PolicyIdentity.from_configuration(name, "1", config),
            event_types,
            evidence_fingerprint,
            confirmation_policy.identity.fingerprint,
        )

    @classmethod
    def new_knowledge(cls, evidence_policy: EvidencePolicy) -> TruthSelectionProfile:
        return cls._create(
            "new-knowledge",
            frozenset({EventType.BRANCH_ACQUISITION}),
            evidence_policy,
            ConfirmationMaskPolicy.main_knowledge_gain(),
        )

    @classmethod
    def refinement(cls, evidence_policy: EvidencePolicy) -> TruthSelectionProfile:
        return cls._create(
            "refinement",
            frozenset({EventType.SPECIFICITY_REFINEMENT}),
            evidence_policy,
            ConfirmationMaskPolicy.main_knowledge_gain(),
        )

    @classmethod
    def combined(cls, evidence_policy: EvidencePolicy) -> TruthSelectionProfile:
        return cls._create(
            "combined",
            frozenset({EventType.BRANCH_ACQUISITION, EventType.SPECIFICITY_REFINEMENT}),
            evidence_policy,
            ConfirmationMaskPolicy.main_knowledge_gain(),
        )

    @classmethod
    def evidence_confirmation(
        cls, evidence_policy: EvidencePolicy
    ) -> TruthSelectionProfile:
        return cls._create(
            "evidence-confirmation",
            frozenset({EventType.EVIDENCE_UPGRADE}),
            evidence_policy,
            ConfirmationMaskPolicy.unmask_upgraded_direct_only(),
        )


@dataclass(frozen=True, slots=True)
class NeutralTruthPolicy:
    identity: PolicyIdentity
    include_iea_only: bool
    include_non_scored_curated: bool

    @classmethod
    def primary_non_electronic_neutral(cls) -> NeutralTruthPolicy:
        config = {"include_iea_only": False, "include_non_scored_curated": True}
        return cls(
            PolicyIdentity.from_configuration(
                "primary_non_electronic_neutral", "1", config
            ),
            False,
            True,
        )

    @classmethod
    def all_usable_neutral(cls) -> NeutralTruthPolicy:
        config = {"include_iea_only": True, "include_non_scored_curated": True}
        return cls(
            PolicyIdentity.from_configuration("all_usable_neutral", "1", config),
            True,
            True,
        )

    def accepts(self, evidence_codes: Iterable[str]) -> bool:
        policy = EvidencePolicy(name="neutral_truth_policy")
        usable = {code for code in evidence_codes if policy.tier(code) is not None}
        return bool(usable) and (self.include_iea_only or usable != {"IEA"})


@dataclass(frozen=True, slots=True)
class DirectTruthTerm:
    sequence_id: str
    aspect: GOAspect
    term_id: str
    origin: SnapshotOrigin
    evidence_codes: frozenset[str]
    selecting_event: EventType | None
    source_assertions: tuple[AnnotationRecord, ...]
    normalized_from: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class DirectTruthSet:
    sequence_id: str
    aspect: GOAspect
    policy_fingerprint: str
    terms: frozenset[str]
    records: tuple[DirectTruthTerm, ...]


def direct_term_from_state(
    state: DirectTermState,
    *,
    origin: SnapshotOrigin,
    selecting_event: EventType | None = None,
) -> DirectTruthTerm:
    return DirectTruthTerm(
        sequence_id=state.sequence_id,
        aspect=GOAspect(state.aspect),
        term_id=state.term_id,
        origin=origin,
        evidence_codes=state.evidence_codes,
        selecting_event=selecting_event,
        source_assertions=state.source_assertions,
        normalized_from=state.normalized_from,
    )
