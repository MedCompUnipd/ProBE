"""Synthetic tests for Stage 07 pure scientific precedence (increment 1)."""

from dataclasses import replace

import pytest

from probe.resolver import (
    MatchedLocusKey,
    ResolverEvidence,
    ResolverPolicy,
    apply_accepted_multiplicity,
    decide,
    is_known_nonidentifying_locus,
    sequence_relation,
    stable_unique_locus,
)


def ev(**changes):
    return replace(ResolverEvidence(scope="PRIMARY", exact_sequence=False), **changes)


@pytest.mark.parametrize(
    "match_type,basis",
    [
        ("PRIMARY", "DIRECT_PRIMARY_ACCESSION"),
        ("SECONDARY", "DIRECT_SECONDARY_ACCESSION"),
    ],
)
def test_exact_direct_continuity(match_type, basis):
    result = decide(
        ev(exact_sequence=True, direct_match_type=match_type, direct_taxonomy_pass=True)
    )
    assert (result.decision, result.decision_basis, result.annotation_donor) == (
        "ACCEPTED",
        basis,
        True,
    )
    assert result.entity_equivalence == "DOCUMENTARY_ACCESSION_CONTINUITY"


def test_nonexact_direct_continuity_overrides_geometry():
    result = decide(
        ev(
            direct_match_type="PRIMARY",
            direct_taxonomy_pass=True,
            stable_locus=True,
            exact_subsequence=False,
        )
    )
    assert result.decision == "ACCEPTED"
    assert result.decision_basis == "DIRECT_ACCESSION_CONTINUITY"
    assert "ACCESSION_CONTINUITY_SEQUENCE_CHANGED" in result.warnings


def test_taxonomy_gate_prevents_noneligible_direct():
    result = decide(ev(direct_match_type="PRIMARY", direct_taxonomy_pass=False))
    assert result.decision == "UNRESOLVED"


def test_nondirect_accession_precedence():
    assert decide(ev(exact_sequence=True, nondirect_accession=True)).decision_basis == (
        "DOCUMENTARY_ACCESSION_PLUS_EXACT_SEQUENCE"
    )
    result = decide(
        ev(
            nondirect_accession=True,
            stable_locus=True,
            exact_subsequence=True,
            exact_resolved_taxid=True,
        )
    )
    assert result.decision == "ACCEPTED"
    assert result.decision_basis == "STABLE_UNIQUE_LOCUS_EXACT_SUBSEQUENCE"


@pytest.mark.parametrize(
    "geometry,basis",
    [
        ({"exact_sequence": True}, "STABLE_UNIQUE_LOCUS_PLUS_EXACT_SEQUENCE"),
        (
            {"exact_subsequence": True, "exact_resolved_taxid": True},
            "STABLE_UNIQUE_LOCUS_EXACT_SUBSEQUENCE",
        ),
        (
            {"equal_length_variant": True, "exact_resolved_taxid": True},
            "STABLE_UNIQUE_LOCUS_EQUAL_LENGTH_VARIANT",
        ),
        ({"exact_subsequence": True}, "STABLE_UNIQUE_LOCUS_OTHER_SEQUENCE_RELATION"),
        ({}, "STABLE_UNIQUE_LOCUS_OTHER_SEQUENCE_RELATION"),
    ],
)
def test_stable_locus_geometries(geometry, basis):
    assert decide(ev(stable_locus=True, **geometry)).decision_basis == basis


def test_partial_report_only_preserves_association():
    e = ev(stable_locus=True, exact_resolved_taxid=True, exact_subsequence=True)
    result = decide(e, ResolverPolicy(partial_sequence="report_only"))
    assert result.decision == "ACCEPTED" and not result.annotation_donor


def test_documentary_fragment_report_only():
    e = ev(exact_sequence=True, nondirect_accession=True, fragment_record=True)
    result = decide(e, ResolverPolicy(documentary_fragment="report_only"))
    assert result.decision == "ACCEPTED" and not result.annotation_donor
    assert "FRAGMENT_RECORD" in result.warnings


def test_fragment_unresolved_is_described_not_rejected():
    result = decide(ev(lineage_present=True, fragment_record=True))
    assert result.decision == "UNRESOLVED"
    assert result.warnings == ("FRAGMENT_RECORD", "WEAK_OR_COLLIDING_LINEAGE")


def test_collision_does_not_override_independent_direct():
    result = decide(
        ev(
            exact_sequence=True,
            direct_match_type="PRIMARY",
            direct_taxonomy_pass=True,
            collision_present=True,
        )
    )
    assert result.decision == "ACCEPTED"
    assert "COLLIDING_DOCUMENTARY_KEY_PRESENT" in result.warnings


def test_exact_sequence_alone_stays_unresolved():
    assert decide(ev(exact_sequence=True)).decision_basis == "EXACT_SEQUENCE_ONLY"
    assert decide(ev(exact_sequence=True, lineage_present=True)).decision_basis == (
        "WEAK_OR_COLLIDING_DOCUMENTARY_PLUS_EXACT_SEQUENCE"
    )


def test_sequence_only_nonexact_is_not_accepted():
    result = decide(ev(exact_subsequence=True))
    assert result.decision_basis == "SEQUENCE_ONLY"
    assert result.decision == "UNRESOLVED"


@pytest.mark.parametrize("token", ["SO:0001217", "SO:1234567"])
def test_sequence_ontology_terms_are_not_loci(token):
    assert is_known_nonidentifying_locus("ORF", token)
    k = MatchedLocusKey("ORF", token, True, True, False, True)
    assert not stable_unique_locus((k,), taxonomy_pass=True)


def test_no_global_fanout_cutoff_or_extra_orf_filter():
    good = MatchedLocusKey("ORF", "CNAG_05404", True, True, False, True)
    collision = MatchedLocusKey("ORF", "FCAALL", True, True, True, True)
    assert stable_unique_locus((good, collision), taxonomy_pass=True)
    assert not stable_unique_locus((collision,), taxonomy_pass=True)


def test_start_end_must_support_same_key():
    a = MatchedLocusKey("ORF", "A", True, False, False, True)
    b = MatchedLocusKey("ORF", "B", False, True, False, True)
    assert not stable_unique_locus((a, b), taxonomy_pass=True)
    assert not stable_unique_locus((a,), taxonomy_pass=True)
    assert not stable_unique_locus((a, b), taxonomy_pass=False)


def test_unverified_04d1_key_is_not_accepted():
    k = MatchedLocusKey("ORF", "A", True, True, False, False)
    assert not stable_unique_locus((k,), taxonomy_pass=True)


def test_strict_audit_only_and_cross_species_unaligned():
    sub = ResolverEvidence(
        scope="TAXONOMY_AUDIT",
        exact_sequence=True,
        audit_reason="SAME_SPECIES_WRONG_SUBTAXON",
        audit_alignment_allowed=True,
        direct_match_type="PRIMARY",
        audited_direct_verified=True,
    )
    cross = ResolverEvidence(
        scope="TAXONOMY_AUDIT",
        exact_sequence=None,
        audit_reason="CROSS_SPECIES",
        audit_alignment_allowed=False,
    )
    assert decide(sub).decision == "AUDIT_ONLY"
    assert decide(cross).decision == "AUDIT_ONLY"
    assert (
        decide(
            cross, ResolverPolicy(subtaxon_policy="include_audited_same_species")
        ).decision
        == "AUDIT_ONLY"
    )
    assert not decide(cross).annotation_donor


def test_promoted_audit_preserves_source_scope_and_requires_verified_direct():
    sub = ResolverEvidence(
        scope="TAXONOMY_AUDIT",
        exact_sequence=True,
        audit_reason="SAME_SPECIES_WRONG_SUBTAXON",
        audit_alignment_allowed=True,
        direct_match_type="SECONDARY",
        audited_direct_verified=True,
    )
    result = decide(sub, ResolverPolicy(subtaxon_policy="include_audited_same_species"))
    assert result.source_scope == "TAXONOMY_AUDIT"
    assert result.evaluation_scope == "PRIMARY"
    assert result.decision_basis == "DIRECT_SECONDARY_ACCESSION"
    assert result.audit_outcome == "EVALUATED_UNDER_AUDITED_SUBTAXON_POLICY"
    with pytest.raises(ValueError, match="independent verified direct anchor"):
        decide(
            replace(sub, audited_direct_verified=False),
            ResolverPolicy(subtaxon_policy="include_audited_same_species"),
        )


def test_multiplicity_applies_only_to_accepted_rows():
    a = decide(
        ev(exact_sequence=True, direct_match_type="PRIMARY", direct_taxonomy_pass=True)
    )
    b = decide(ev(exact_sequence=True, nondirect_accession=True))
    unresolved = decide(ev(exact_sequence=True))
    rows = [("r", "t", "2", a), ("r", "t", "1", b), ("r", "t", "3", unresolved)]
    updated = apply_accepted_multiplicity(rows)
    assert len(updated) == 3
    assert all(r[3].accepted_candidates_for_target_release == 2 for r in updated)
    assert all("MULTIPLE_ACCEPTED_CANDIDATES" in r[3].warnings for r in updated[:2])
    assert "MULTIPLE_ACCEPTED_CANDIDATES" not in updated[2][3].warnings


def test_duplicate_triples_fail_closed():
    r = decide(ev())
    with pytest.raises(ValueError, match="Duplicate candidate triple"):
        apply_accepted_multiplicity([("r", "t", "1", r), ("r", "t", "1", r)])


def test_descriptive_rescue_exception_cannot_cause_acceptance():
    kwargs = dict(
        exact_sequence=False,
        aligned=True,
        decision_basis="SEQUENCE_ONLY",
        exact_subsequence=True,
        equal_length_variant=False,
    )
    assert (
        sequence_relation(go_positive_sequence_only=True, **kwargs)
        == "NONEXACT_SEQUENCE_ONLY"
    )
    assert sequence_relation(go_positive_sequence_only=False, **kwargs) == (
        "CANDIDATE_EXACT_CONTIGUOUS_SUBSEQUENCE"
    )
    assert decide(ev(exact_subsequence=True)).decision == "UNRESOLVED"


def test_deterministic_warnings_sorted_once():
    result = decide(
        ev(fragment_record=True, collision_present=True, lineage_present=True)
    )
    assert result.warnings == tuple(sorted(set(result.warnings)))


def test_invalid_audit_geometry_fails_closed():
    with pytest.raises(ValueError):
        ResolverEvidence(
            scope="TAXONOMY_AUDIT",
            audit_reason="CROSS_SPECIES",
            audit_alignment_allowed=False,
            exact_sequence=True,
        )


def test_bad_policy_fails_closed():
    with pytest.raises(ValueError):
        ResolverPolicy(subtaxon_policy="same_species")


def test_nonexact_stable_variant_outranks_weak_accession():
    evidence = ev(
        nondirect_accession=True,
        stable_locus=True,
        exact_resolved_taxid=True,
        equal_length_variant=True,
    )
    result = decide(evidence)
    assert result.decision == "ACCEPTED"
    assert result.decision_basis == "STABLE_UNIQUE_LOCUS_EQUAL_LENGTH_VARIANT"


def test_exact_nondirect_accession_preserves_exact_precedence():
    evidence = ev(
        exact_sequence=True,
        nondirect_accession=True,
        stable_locus=True,
    )
    result = decide(evidence)
    assert result.decision == "ACCEPTED"
    assert result.decision_basis == "DOCUMENTARY_ACCESSION_PLUS_EXACT_SEQUENCE"


@pytest.mark.parametrize("token", ["SO%3A0001217", "SO%3a0001217"])
def test_encoded_sequence_ontology_is_not_a_locus(token):
    assert is_known_nonidentifying_locus("ORF", token)
    key = MatchedLocusKey("ORF", token, True, True, False, True)
    assert not stable_unique_locus((key,), taxonomy_pass=True)


@pytest.mark.parametrize(
    "token",
    ["SO%253A0001217", "XSO%3A0001217", "CNAG_05404"],
)
def test_percent_decoding_does_not_disqualify_unrelated_orf(token):
    assert not is_known_nonidentifying_locus("ORF", token)


def test_valid_independent_locus_survives_encoded_so_key():
    ontology = MatchedLocusKey("ORF", "SO%3A0001217", True, True, False, True)
    valid = MatchedLocusKey("ORF", "CNAG_05404", True, True, False, True)
    assert stable_unique_locus((ontology, valid), taxonomy_pass=True)
