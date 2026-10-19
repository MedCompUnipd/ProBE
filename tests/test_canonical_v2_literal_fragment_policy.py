"""Canonical-only fragment literal identity, preserving frozen 2C behavior."""

from __future__ import annotations

import pytest

from probe.canonical_v2.fragment_literal_policy import (
    DECISION_BASIS,
    promote_canonical_literal_fragments,
)
from probe.resolver_alignments import JoinedAlignment
from probe.resolver_anchors import JoinedDirect
from probe.resolver_candidates import CandidateProvenance, CandidateRecord
from probe.stage2c_resolver_variant import resolve_stage2c_variant

RELEASES = ("2023_01", "2026_03")
SEQ = "ACDEFGHIKLMN"


def case(
    *,
    sequence=SEQ,
    target_taxid="100",
    candidate_taxid="100",
    fragment=True,
    exact=True,
    documentary=False,
    scope="PRIMARY",
    rid="1",
):
    candidate = CandidateRecord(
        release=RELEASES[0],
        target_id="T1",
        rid=rid,
        scope=scope,
        target_accession="P1",
        candidate_accession="P1" if documentary else "Q1",
        candidate_raw_ox=candidate_taxid,
        candidate_resolved_taxid=candidate_taxid,
        candidate_species_taxid="10",
        primary_bucket="S:10",
        fragment_record=fragment,
        discovery_channel="CANONICAL_DOCUMENTARY_V2",
        sequence_discovered=False,
        go_positive_sequence_discovered=False,
        full_v2_rescue_sequence_discovered=False,
        lineage_discovered=False,
        lineage_key_count=0,
        lineage_collision_present=False,
        audit=None,
    )
    direct = JoinedDirect(
        direct_match_type="PRIMARY" if documentary else None,
        direct_taxonomy_pass=documentary,
        audited_direct_verified=False,
        exact_resolved_taxid=target_taxid == candidate_taxid,
        target_resolved_taxid=target_taxid,
        candidate_resolved_taxid=candidate_taxid,
        original_04b_key_source_eligible=(True if documentary else None),
        taxonomy_relation=None,
    )
    aln = JoinedAlignment(
        aligned=True,
        exact_sequence=exact,
        target_length=len(sequence),
        candidate_length=len(sequence),
        exact_subsequence=False,
        equal_length_variant=not exact,
        target_global=None,
        candidate_global=None,
    )
    key = candidate.key
    provenance = {key: CandidateProvenance(candidate, direct, None)}
    alignments = {key: aln}
    taxonomy = {(RELEASES[0], "T1"): (target_taxid, "10")}
    pair = {key: (sequence, sequence if exact else sequence[:-1] + "A")}
    return provenance, alignments, taxonomy, pair


def evaluate(data, *, documentary_fragment="allow", strain_policy="strict"):
    provenance, alignments, taxonomy, sequences = data
    before = resolve_stage2c_variant(
        provenance=provenance,
        alignments=alignments,
        taxonomy=taxonomy,
        sequences=sequences,
        release_order=RELEASES,
        strain_policy=strain_policy,
        documentary_fragment=documentary_fragment,
    )
    after = promote_canonical_literal_fragments(
        result=before,
        provenance=provenance,
        alignments=alignments,
        sequences=sequences,
        documentary_fragment=documentary_fragment,
        strain_policy=strain_policy,
        taxonomy=taxonomy,
    )
    return next(iter(before.rows.values())), next(iter(after.rows.values()))


def test_same_taxid_fragment_without_strong_documentary_is_donor():
    old, new = evaluate(case())
    assert old.decision.decision == "UNRESOLVED"
    assert new.decision.decision == "ACCEPTED"
    assert new.decision.annotation_donor is True
    assert new.decision.decision_basis == DECISION_BASIS
    assert "FRAGMENT_RECORD" in new.decision.warnings
    assert "SAME_TAXID_LITERAL_EXACT_FRAGMENT_PROOF" in new.decision.warnings
    assert new.candidate.fragment_record is True


@pytest.mark.parametrize("residue", ["B", "J", "X", "Z", "U", "O"])
def test_ambiguous_exact_fragment_is_donor(residue):
    sequence = SEQ[:4] + residue + SEQ[5:]
    _, new = evaluate(case(sequence=sequence))
    assert new.decision.annotation_donor is True


def test_report_only_accepted_but_not_donor():
    _, new = evaluate(case(), documentary_fragment="report_only")
    assert new.decision.decision == "ACCEPTED"
    assert new.decision.annotation_donor is False


def test_existing_documentary_decision_preserved():
    old, new = evaluate(case(documentary=True))
    assert old.decision.decision == "ACCEPTED"
    assert new == old


def test_nonexact_fragment_not_promoted():
    old, new = evaluate(case(exact=False))
    assert new == old
    assert not new.decision.annotation_donor


def test_cross_taxid_fragment_not_promoted():
    old, new = evaluate(case(candidate_taxid="101"))
    assert new == old
    assert not new.decision.annotation_donor


def test_nonfragment_unchanged():
    old, new = evaluate(case(fragment=False))
    assert new == old


def test_missing_sequence_fails_closed():
    data = case()
    data[3].clear()
    with pytest.raises(ValueError, match="same-TaxID proof|sequence proof"):
        evaluate(data)


def test_incorrect_literal_sequence_fails_closed():
    data = case()
    key = next(iter(data[3]))
    data[3][key] = (SEQ, SEQ[:-1] + "A")
    with pytest.raises(ValueError, match="sequence conflict"):
        evaluate(data)


def test_invalid_stop_marker_fails_closed():
    data = case(sequence="ACDEF*HIKLMN")
    with pytest.raises(ValueError, match="alphabet"):
        evaluate(data)


def test_incomplete_alignment_coverage_fails_closed():
    data = case()
    provenance, alignments, taxonomy, sequence = data
    before = resolve_stage2c_variant(
        provenance=provenance,
        alignments=alignments,
        taxonomy=taxonomy,
        sequences=sequence,
        release_order=RELEASES,
        strain_policy="strict",
    )
    with pytest.raises(ValueError, match="Incomplete"):
        promote_canonical_literal_fragments(
            result=before,
            provenance=provenance,
            alignments={},
            sequences=sequence,
            documentary_fragment="allow",
            strain_policy="strict",
            taxonomy=taxonomy,
        )


def test_policy_rejected_if_unknown():
    data = case()
    provenance, alignments, taxonomy, sequence = data
    before = resolve_stage2c_variant(
        provenance=provenance,
        alignments=alignments,
        taxonomy=taxonomy,
        sequences=sequence,
        release_order=RELEASES,
        strain_policy="strict",
    )
    with pytest.raises(ValueError, match="policy"):
        promote_canonical_literal_fragments(
            result=before,
            provenance=provenance,
            alignments=alignments,
            sequences=sequence,
            documentary_fragment="unexpected",
            strain_policy="strict",
            taxonomy=taxonomy,
        )


def test_different_lengths_are_not_literal_exact():
    data = case(exact=False)
    key = next(iter(data[3]))
    data[3][key] = (SEQ, SEQ[:-1])
    old, new = evaluate(data)
    assert old == new
    assert new.decision.annotation_donor is False


def test_target_taxid_missing_never_promotes():
    data = case(target_taxid=None, candidate_taxid="100")
    old, new = evaluate(data)
    assert old == new
    assert new.decision.annotation_donor is False


def test_same_species_fragment_literal_exact_policy():
    _, row = evaluate(
        case(candidate_taxid="101"),
        strain_policy="same_species_exact",
    )
    assert row.decision.decision == "ACCEPTED"
    assert row.decision.annotation_donor is True
    assert row.decision.decision_basis == (
        "CANONICAL_SAME_SPECIES_LITERAL_EXACT_FRAGMENT"
    )
