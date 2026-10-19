"""Scientific contract: one shared discovery, two cross-strain donor policies."""

from __future__ import annotations

from dataclasses import replace

import pytest

from probe.resolver_alignments import JoinedAlignment
from probe.resolver_anchors import AuditDirectReference, JoinedDirect
from probe.resolver_candidates import CandidateProvenance, CandidateRecord
from probe.stage2c_resolver_variant import resolve_stage2c_variant

RELEASES = ("2023_01", "2026_03")
SEQ = "ACDEFGHIKLMN"


def fixture(
    *,
    candidate_taxid="200",
    exact=True,
    fragment=False,
    audit=False,
    documentary=False,
    species="10",
    rid="1",
):
    verified = (
        AuditDirectReference(
            audit_class="TAXONOMY_RESCUE_AUDIT",
            reason="SAME_SPECIES_WRONG_SUBTAXON",
            alignment_allowed=True,
            direct_match_type="PRIMARY",
            direct_match_accession="P1",
            taxonomy_relation="SAME_SPECIES_DIFFERENT_SUBTAXON",
            target_resolved_taxid="100",
            anchor_resolved_taxid=candidate_taxid,
        )
        if audit
        else None
    )
    record = CandidateRecord(
        release=RELEASES[0],
        target_id="T1",
        rid=rid,
        scope="TAXONOMY_AUDIT" if audit else "PRIMARY",
        target_accession="P1",
        candidate_accession="P1" if documentary else "Q1",
        candidate_raw_ox=candidate_taxid,
        candidate_resolved_taxid=candidate_taxid,
        candidate_species_taxid=species,
        primary_bucket="S:10",
        fragment_record=fragment,
        discovery_channel=None if audit else "SEQUENCE_ONLY",
        sequence_discovered=not audit,
        go_positive_sequence_discovered=not audit,
        full_v2_rescue_sequence_discovered=False,
        lineage_discovered=False,
        lineage_key_count=0,
        lineage_collision_present=False,
        audit=verified,
    )
    direct = JoinedDirect(
        direct_match_type="PRIMARY" if documentary else None,
        direct_taxonomy_pass=documentary and not audit,
        audited_direct_verified=documentary and audit,
        exact_resolved_taxid=candidate_taxid == "100",
        target_resolved_taxid="100",
        candidate_resolved_taxid=candidate_taxid,
        original_04b_key_source_eligible=(
            False if audit else (True if documentary else None)
        ),
        taxonomy_relation=("SAME_SPECIES_DIFFERENT_SUBTAXON" if audit else None),
    )
    aln = JoinedAlignment(
        aligned=True,
        exact_sequence=exact,
        target_length=len(SEQ),
        candidate_length=len(SEQ),
        exact_subsequence=False,
        equal_length_variant=not exact,
        target_global=None,
        candidate_global=None,
    )
    key = record.key
    return (
        {key: CandidateProvenance(record, direct, None)},
        {key: aln},
        {key[:2]: ("100", "10")},
        {key: (SEQ, SEQ if exact else SEQ[:-1] + "A")},
    )


def run(case, *, policy="same_species_exact", **changes):
    provenance, alignments, taxonomy, proofs = case
    return resolve_stage2c_variant(
        provenance=provenance,
        alignments=alignments,
        taxonomy=taxonomy,
        sequences=proofs,
        release_order=RELEASES,
        strain_policy=policy,
        **changes,
    )


def decision(case, **options):
    return next(iter(run(case, **options).rows.values())).decision


def test_strict_denies_exact_cross_strain():
    result = decision(fixture(), policy="strict")
    assert result.decision == "UNRESOLVED"
    assert result.annotation_donor is False


def test_same_species_exact_without_locus_or_accession_is_donor():
    result = decision(fixture())
    assert result.decision == "ACCEPTED"
    assert result.annotation_donor is True
    assert result.decision_basis == "SAME_SPECIES_LITERAL_EXACT_SEQUENCE"


def test_same_species_exact_with_verified_documentary_continuity_is_donor():
    result = decision(fixture(documentary=True))
    assert result.decision == "ACCEPTED"
    assert result.annotation_donor is True
    assert result.decision_basis == "SAME_SPECIES_LITERAL_EXACT_SEQUENCE"


def test_verified_audited_anchor_may_donate_only_under_exact_policy():
    result = decision(fixture(audit=True, documentary=True))
    assert result.decision == "ACCEPTED"
    assert result.annotation_donor is True


def test_nonidentical_cross_strain_is_not_donor_even_with_direct_anchor():
    result = decision(fixture(exact=False, documentary=True))
    assert result.decision == "UNRESOLVED"
    assert result.annotation_donor is False


def test_nonidentical_cross_strain_is_denied_under_strict():
    result = decision(fixture(exact=False), policy="strict")
    assert result.annotation_donor is False


@pytest.mark.parametrize("strain_policy", ("strict", "same_species_exact"))
def test_same_taxid_exact_without_locus_is_donor(strain_policy):
    row = decision(
        fixture(candidate_taxid="100", documentary=False),
        policy=strain_policy,
    )
    assert row.decision == "ACCEPTED"
    assert row.annotation_donor
    assert row.decision_basis == "SAME_TAXID_LITERAL_EXACT_SEQUENCE"


@pytest.mark.parametrize("strain_policy", ("strict", "same_species_exact"))
def test_same_taxid_nonexact_without_locus_remains_unresolved(strain_policy):
    row = decision(
        fixture(candidate_taxid="100", exact=False, documentary=False),
        policy=strain_policy,
    )
    assert row.decision == "UNRESOLVED"
    assert not row.annotation_donor


def test_same_taxid_exact_preserves_direct_documentary_decision():
    row = decision(fixture(candidate_taxid="100", documentary=True))
    assert row.decision == "ACCEPTED"
    assert row.decision_basis == "DIRECT_PRIMARY_ACCESSION"


@pytest.mark.parametrize("residue", ("B", "Z", "J", "X"))
@pytest.mark.parametrize("strain_policy", ("strict", "same_species_exact"))
def test_same_taxid_iupac26_literal_exact_is_donor(residue, strain_policy):
    case = fixture(candidate_taxid="100", documentary=False)
    key = next(iter(case[3]))
    sequence = SEQ[:4] + residue + SEQ[5:]
    case[3][key] = (sequence, sequence)
    row = decision(case, policy=strain_policy)
    assert row.decision == "ACCEPTED"
    assert row.annotation_donor
    assert row.decision_basis == "SAME_TAXID_LITERAL_EXACT_SEQUENCE"
    assert "SAME_TAXID_LITERAL_EXACT_PROOF" in row.warnings


@pytest.mark.parametrize("invalid", ("*", "?", "-", "a"))
def test_same_taxid_invalid_alphabet_exact_is_not_donor(invalid):
    case = fixture(candidate_taxid="100", documentary=False)
    key = next(iter(case[3]))
    sequence = SEQ[:4] + invalid + SEQ[5:]
    case[3][key] = (sequence, sequence)
    row = decision(case, policy="strict")
    assert row.decision == "UNRESOLVED"
    assert not row.annotation_donor


def test_same_taxid_missing_literal_proof_fails_closed():
    case = fixture(candidate_taxid="100", documentary=False)
    case[3].clear()
    with pytest.raises(ValueError, match="Missing literal same-TaxID proof"):
        run(case, policy="strict")


def test_same_taxid_inconsistent_literal_proof_fails_closed():
    case = fixture(candidate_taxid="100", documentary=False)
    key = next(iter(case[3]))
    case[3][key] = (SEQ, SEQ[:-1] + "A")
    with pytest.raises(ValueError, match="literal sequence conflict"):
        run(case, policy="strict")


def test_same_taxid_fragment_requires_independent_support():
    row = decision(fixture(candidate_taxid="100", fragment=True))
    assert row.decision == "UNRESOLVED"
    assert not row.annotation_donor


def test_same_resolved_taxid_keeps_nonidentical_documentary_legacy():
    case = fixture(candidate_taxid="100", exact=False, documentary=True)
    for strain_policy in ("strict", "same_species_exact"):
        result = decision(case, policy=strain_policy)
        assert result.decision == "ACCEPTED"
        assert result.annotation_donor is True


def test_cross_species_never_donates():
    result = decision(fixture(species="11"))
    assert result.annotation_donor is False


@pytest.mark.parametrize("residue", ("B", "Z", "J", "X"))
@pytest.mark.parametrize("documentary", (False, True))
def test_literal_iupac26_cross_strain_exact_is_donor(residue, documentary):
    case = fixture(documentary=documentary)
    key = next(iter(case[3]))
    sequence = SEQ[:4] + residue + SEQ[5:]
    case[3][key] = (sequence, sequence)
    row = decision(case)
    assert row.decision == "ACCEPTED"
    assert row.annotation_donor
    assert row.decision_basis == "SAME_SPECIES_LITERAL_EXACT_SEQUENCE"
    assert "CROSS_STRAIN_LITERAL_EXACT_PROOF" in row.warnings


def test_fragment_without_independent_document_never_donates():
    result = decision(fixture(fragment=True))
    assert result.annotation_donor is False
    assert result.decision == "UNRESOLVED"


@pytest.mark.parametrize(
    "fragment_policy,donor",
    [
        ("allow", True),
        ("report_only", False),
    ],
)
def test_audited_fragment_requires_policy(fragment_policy, donor):
    result = decision(
        fixture(fragment=True, audit=True, documentary=True),
        documentary_fragment=fragment_policy,
    )
    assert result.decision == "ACCEPTED"
    assert result.annotation_donor is donor


def test_strict_audited_cross_strain_stays_audit_only():
    result = decision(
        fixture(fragment=False, audit=True, documentary=True),
        policy="strict",
    )
    assert result.decision == "AUDIT_ONLY"
    assert result.annotation_donor is False


def test_mismatched_literal_sequence_and_06a_is_rejected():
    case = fixture(exact=True)
    key = next(iter(case[3]))
    case[3][key] = (SEQ, SEQ[:-1] + "A")
    with pytest.raises(ValueError, match="literal sequence conflict"):
        run(case)


def test_missing_literal_cross_strain_evidence_is_rejected():
    case = fixture()
    case[3].clear()
    with pytest.raises(ValueError, match="Missing literal"):
        run(case)


def test_missing_target_taxonomy_is_rejected():
    case = fixture()
    case[2].clear()
    with pytest.raises(ValueError, match="Missing verified"):
        run(case)


def test_contradictory_direct_taxonomy_is_rejected():
    case = fixture()
    key = next(iter(case[0]))
    facts = case[0][key]
    case[0][key] = replace(
        facts, direct=replace(facts.direct, target_resolved_taxid="999")
    )
    with pytest.raises(ValueError, match="TaxID conflict"):
        run(case)


def test_policy_must_be_declared():
    with pytest.raises(ValueError, match="Unsupported"):
        run(fixture(), policy="unknown")


def test_input_provenance_not_mutated():
    case = fixture()
    before = dict(case[0])
    result = run(case)
    assert case[0] == before
    assert result.cross_strain_total == 1
    assert result.cross_strain_donors == 1


def test_exact_cross_strain_is_policy_specific_not_documentary():
    assert not decision(fixture(), policy="strict").annotation_donor
    assert decision(fixture(), policy="same_species_exact").annotation_donor


@pytest.mark.parametrize("invalid", ("*", "?", "-", "a"))
@pytest.mark.parametrize("documentary", (False, True))
def test_invalid_alphabet_cross_strain_exact_is_denied(invalid, documentary):
    case = fixture(documentary=documentary)
    key = next(iter(case[3]))
    sequence = SEQ[:4] + invalid + SEQ[5:]
    case[3][key] = (sequence, sequence)
    row = decision(case)
    assert row.decision == "UNRESOLVED"
    assert not row.annotation_donor
    assert "INVALID_PROTEIN_ALPHABET" in row.warnings


def test_unresolved_taxonomy_cannot_keep_canonical_donor():
    case = fixture(candidate_taxid=None, documentary=True)
    result = decision(case)
    assert result.decision == "UNRESOLVED"
    assert result.annotation_donor is False
    assert "UNRESOLVED_TAXONOMY" in result.warnings


def test_final_multiplicity_is_not_stale_after_strict_denial():
    first = fixture(candidate_taxid="100", documentary=True, rid="1")
    second = fixture(candidate_taxid="200", documentary=True, rid="2")
    for index in (0, 1, 3):
        first[index].update(second[index])
    result = run(first, policy="strict")
    accepted = result.rows[(RELEASES[0], "T1", "1")].decision
    denied = result.rows[(RELEASES[0], "T1", "2")].decision
    assert accepted.decision == "ACCEPTED"
    assert accepted.accepted_candidates_for_target_release == 1
    assert "MULTIPLE_ACCEPTED_CANDIDATES" not in accepted.warnings
    assert denied.decision == "UNRESOLVED"
    assert denied.accepted_candidates_for_target_release == 1


def primary_overlap(*, exact: bool = True, documentary: bool = True):
    case = fixture(audit=True, documentary=documentary, exact=exact)
    key = next(iter(case[0]))
    facts = case[0][key]
    candidate = facts.candidate
    case[0][key] = replace(
        facts,
        candidate=replace(
            candidate,
            scope="PRIMARY",
            audit=None,
            overlap_audit=candidate.audit,
            discovery_channel="STAGE2C_GO_AND_FULL",
            sequence_discovered=True,
            go_positive_sequence_discovered=True,
            full_v2_rescue_sequence_discovered=True,
        ),
    )
    return case


def test_exact_primary_audit_overlap_keeps_both_provenances():
    case = primary_overlap()
    strict = run(case, policy="strict")
    same = run(case, policy="same_species_exact")
    key = next(iter(case[0]))
    assert strict.rows[key].candidate.scope == "PRIMARY"
    assert strict.rows[key].candidate.overlap_audit is not None
    assert not strict.rows[key].decision.annotation_donor
    assert same.rows[key].decision.annotation_donor
    assert same.rows[key].tsv_row()["audit_reason"] == "SAME_SPECIES_WRONG_SUBTAXON"
    assert same.rows[key].decision.audit_outcome == (
        "EVALUATED_UNDER_CROSS_STRAIN_EXACT_POLICY"
    )


def test_nonexact_primary_audit_overlap_cannot_cross_transfer():
    assert not decision(primary_overlap(exact=False)).annotation_donor


def test_primary_overlap_without_independent_04b_is_rejected():
    case = primary_overlap(documentary=False)
    with pytest.raises(ValueError, match="audited direct accession type mismatch"):
        run(case)


def test_full_presence_and_go_positive_are_release_specific_facts():
    start = fixture(candidate_taxid="100", exact=True, documentary=False)
    start_key = next(iter(start[0]))
    start_fact = start[0][start_key]
    start[0][start_key] = replace(
        start_fact,
        candidate=replace(
            start_fact.candidate,
            discovery_channel="STAGE2C_FULL_ONLY",
            go_positive_sequence_discovered=False,
            full_v2_rescue_sequence_discovered=True,
        ),
    )
    end_key = (RELEASES[1], start_key[1], start_key[2])
    end_fact = replace(
        start[0][start_key],
        candidate=replace(
            start[0][start_key].candidate,
            release=RELEASES[1],
            discovery_channel="STAGE2C_GO_AND_FULL",
            go_positive_sequence_discovered=True,
        ),
    )
    start[0][end_key] = end_fact
    start[1][end_key] = start[1][start_key]
    start[2][end_key[:2]] = start[2][start_key[:2]]
    start[3][end_key] = start[3][start_key]
    resolved = run(start)
    assert len(resolved.rows) == 2
    assert resolved.rows[start_key].candidate.full_v2_rescue_sequence_discovered
    assert not resolved.rows[start_key].candidate.go_positive_sequence_discovered
    assert resolved.rows[end_key].candidate.go_positive_sequence_discovered
    assert all(row.decision.annotation_donor for row in resolved.rows.values())


def test_cross_strain_exact_adds_to_same_taxid_direct_and_recounts():
    same = fixture(candidate_taxid="100", documentary=True, rid="1")
    cross = fixture(candidate_taxid="200", documentary=False, rid="2")
    for index in (0, 1, 3):
        same[index].update(cross[index])
    outcome = run(same)
    assert len(outcome.rows) == 2
    assert outcome.cross_strain_donors == 1
    for row in outcome.rows.values():
        assert row.decision.annotation_donor
        assert row.decision.accepted_candidates_for_target_release == 2
        assert "MULTIPLE_ACCEPTED_CANDIDATES" in row.decision.warnings


def test_cross_strain_full_exact_go_negative_can_still_be_candidate():
    case = fixture(documentary=False)
    key = next(iter(case[0]))
    fact = case[0][key]
    case[0][key] = replace(
        fact,
        candidate=replace(
            fact.candidate,
            discovery_channel="STAGE2C_FULL_EXACT",
            go_positive_sequence_discovered=False,
            full_v2_rescue_sequence_discovered=True,
        ),
    )
    row = run(case).rows[key]
    assert row.decision.annotation_donor
    assert not row.candidate.go_positive_sequence_discovered
    assert row.candidate.full_v2_rescue_sequence_discovered
