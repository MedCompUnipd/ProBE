"""Cross-check Stage 07 metadata against the actual decision engine."""

from __future__ import annotations

from probe.resolver import ResolverEvidence, ResolverPolicy, decide
from probe.resolver_metadata import RULE_PRECEDENCE, build_selection_metadata


def test_all_documented_decision_branches():
    primary = "PRIMARY"
    audit = "TAXONOMY_AUDIT"

    cases = (
        ResolverEvidence(
            scope=audit,
            exact_sequence=None,
            audit_reason="CROSS_SPECIES",
            audit_alignment_allowed=False,
        ),
        ResolverEvidence(
            scope=audit,
            exact_sequence=True,
            audit_reason="SAME_SPECIES_WRONG_SUBTAXON",
            audit_alignment_allowed=True,
        ),
        ResolverEvidence(
            scope=primary,
            exact_sequence=True,
            direct_match_type="PRIMARY",
            direct_taxonomy_pass=True,
        ),
        ResolverEvidence(
            scope=primary,
            exact_sequence=True,
            direct_match_type="SECONDARY",
            direct_taxonomy_pass=True,
        ),
        ResolverEvidence(
            scope=primary,
            exact_sequence=False,
            direct_match_type="PRIMARY",
            direct_taxonomy_pass=True,
        ),
        ResolverEvidence(
            scope=primary,
            exact_sequence=True,
            nondirect_accession=True,
        ),
        ResolverEvidence(
            scope=primary,
            exact_sequence=False,
            nondirect_accession=True,
        ),
        ResolverEvidence(
            scope=primary,
            exact_sequence=True,
            stable_locus=True,
        ),
        ResolverEvidence(
            scope=primary,
            exact_sequence=False,
            stable_locus=True,
            exact_resolved_taxid=True,
            exact_subsequence=True,
        ),
        ResolverEvidence(
            scope=primary,
            exact_sequence=False,
            stable_locus=True,
            exact_resolved_taxid=True,
            equal_length_variant=True,
        ),
        ResolverEvidence(
            scope=primary,
            exact_sequence=False,
            stable_locus=True,
        ),
        ResolverEvidence(
            scope=primary,
            exact_sequence=True,
            lineage_present=True,
        ),
        ResolverEvidence(
            scope=primary,
            exact_sequence=True,
        ),
        ResolverEvidence(
            scope=primary,
            exact_sequence=False,
            lineage_present=True,
        ),
        ResolverEvidence(
            scope=primary,
            exact_sequence=False,
        ),
    )

    assert len(cases) == len(RULE_PRECEDENCE) == 15

    for index, evidence in enumerate(cases):
        _, documented_decision, documented_basis = RULE_PRECEDENCE[index]

        actual = decide(evidence, ResolverPolicy())

        assert actual.decision == documented_decision, index
        assert actual.decision_basis == documented_basis, index

    metadata = build_selection_metadata(
        start_release_id="2023_01",
        end_release_id="2026_03",
        policy=ResolverPolicy(),
        policy_version="stage07-resolver-v1",
    )

    documented = tuple(
        (
            rule["condition"],
            rule["decision"],
            rule["decision_basis"],
        )
        for rule in metadata["decision_rule_precedence"]
    )

    assert documented == RULE_PRECEDENCE


def test_optional_audit_promotion_matches_metadata():
    policy = ResolverPolicy(subtaxon_policy="include_audited_same_species")

    evidence = ResolverEvidence(
        scope="TAXONOMY_AUDIT",
        exact_sequence=True,
        audit_reason="SAME_SPECIES_WRONG_SUBTAXON",
        audit_alignment_allowed=True,
        direct_match_type="PRIMARY",
        audited_direct_verified=True,
    )

    decision = decide(evidence, policy)

    assert decision.source_scope == "TAXONOMY_AUDIT"
    assert decision.evaluation_scope == "PRIMARY"
    assert decision.decision == "ACCEPTED"
    assert decision.decision_basis == "DIRECT_PRIMARY_ACCESSION"
    assert decision.audit_outcome == ("EVALUATED_UNDER_AUDITED_SUBTAXON_POLICY")

    metadata = build_selection_metadata(
        start_release_id="2023_01",
        end_release_id="2026_03",
        policy=policy,
        policy_version="stage07-resolver-v1",
    )

    assert (
        metadata["effective_policy_effects"]["audited_same_species"]["evaluated"]
        is True
    )

    assert metadata["effective_policies"]["resolver"] == {
        "subtaxon_policy": "include_audited_same_species"
    }
