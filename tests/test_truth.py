from __future__ import annotations

from probe.evidence import EvidencePolicy
from probe.knowledge import EventType
from probe.relations import PolicyIdentity
from probe.truth import (
    NeutralTruthPolicy,
    PositiveAssertionPolicy,
    TruthSelectionProfile,
    evidence_policy_fingerprint,
)


def test_named_event_profiles_are_disjoint_and_explicit():
    evidence = EvidencePolicy.experimental_strict()

    assert TruthSelectionProfile.new_knowledge(evidence).event_types == {
        EventType.BRANCH_ACQUISITION
    }
    assert TruthSelectionProfile.refinement(evidence).event_types == {
        EventType.SPECIFICITY_REFINEMENT
    }
    assert TruthSelectionProfile.combined(evidence).event_types == {
        EventType.BRANCH_ACQUISITION,
        EventType.SPECIFICITY_REFINEMENT,
    }
    assert TruthSelectionProfile.evidence_confirmation(evidence).event_types == {
        EventType.EVIDENCE_UPGRADE
    }


def test_prior_and_neutral_evidence_policies_keep_iea_choice_separate():
    prior = PositiveAssertionPolicy.all_usable_positive_t0()
    primary = NeutralTruthPolicy.primary_non_electronic_neutral()
    sensitivity = NeutralTruthPolicy.all_usable_neutral()

    assert prior.accepts({"IEA"})
    assert not prior.accepts({"NAS", "ND"})
    assert not primary.accepts({"IEA"})
    assert primary.accepts({"IEA", "IDA"})
    assert sensitivity.accepts({"IEA"})


def test_policy_fingerprints_are_deterministic_and_semantic():
    first = PolicyIdentity.from_configuration("policy", "1", {"b": 2, "a": 1})
    reordered = PolicyIdentity.from_configuration("policy", "1", {"a": 1, "b": 2})
    changed = PolicyIdentity.from_configuration("policy", "1", {"a": 1, "b": 3})

    assert first.fingerprint == reordered.fingerprint
    assert first.fingerprint != changed.fingerprint
    assert evidence_policy_fingerprint(EvidencePolicy.experimental_strict()) == (
        evidence_policy_fingerprint(EvidencePolicy.experimental_strict())
    )
