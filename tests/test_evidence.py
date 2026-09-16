from __future__ import annotations

from itertools import pairwise

from probe import EvidencePolicy, EvidenceTier


def test_evidence_tiers_and_best_tier_preserve_operational_order():
    policy = EvidencePolicy.experimental_strict()

    assert policy.tier("IDA") is EvidenceTier.EXPERIMENTAL_TRADITIONAL
    assert policy.tier("HDA") is EvidenceTier.EXPERIMENTAL_HIGH_THROUGHPUT
    assert policy.tier("IBA") is EvidenceTier.PHYLOGENETIC_CURATED
    assert policy.tier("TAS") is EvidenceTier.TRACEABLE_CURATED_STATEMENT
    assert policy.tier("RCA") is EvidenceTier.COMPUTATIONAL_CURATED
    assert policy.tier("IEA") is EvidenceTier.ELECTRONIC
    assert policy.tier("NAS") is None
    assert policy.tier("ND") is None
    assert (
        policy.best_tier({"IEA", "NAS", "IMP"}) is EvidenceTier.EXPERIMENTAL_TRADITIONAL
    )


def test_evidence_profiles_are_cumulative_and_exclude_unusable_codes():
    profiles = (
        EvidencePolicy.experimental_strict(),
        EvidencePolicy.experimental_all(),
        EvidencePolicy.curated_phylogenetic(),
        EvidencePolicy.curated_traceable(),
        EvidencePolicy.curated_non_electronic(),
    )

    for narrower, broader in pairwise(profiles):
        assert narrower.accepted_tiers < broader.accepted_tiers

    widest = profiles[-1]
    assert widest.accepts(frozenset({"RCA"}))
    assert not widest.accepts(frozenset({"IEA"}))
    assert not widest.accepts(frozenset({"NAS", "ND"}))


def test_experimental_compatibility_policy_includes_high_throughput():
    compatibility = EvidencePolicy.experimental()
    explicit = EvidencePolicy.experimental_all()

    assert compatibility.accepted_tiers == explicit.accepted_tiers
    assert compatibility.accepts(frozenset({"HGI"}))
    assert not EvidencePolicy.experimental_strict().accepts(frozenset({"HGI"}))


def test_upgrade_requires_crossing_the_active_profile_threshold():
    strict = EvidencePolicy.experimental_strict()

    assert strict.is_upgrade(frozenset({"IEA"}), frozenset({"IDA"}))
    assert not strict.is_upgrade(frozenset({"EXP"}), frozenset({"IDA"}))
    assert not strict.is_upgrade(frozenset({"IEA", "IMP"}), frozenset({"IDA"}))
    assert not strict.is_upgrade(frozenset({"IEA"}), frozenset({"HDA"}))
