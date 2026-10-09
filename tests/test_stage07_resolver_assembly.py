"""Synthetic Stage 07 assembly, strict audit, multiplicity and output tests."""

from __future__ import annotations

from dataclasses import replace

import pytest

from probe.resolver import MatchedLocusKey, ResolverPolicy
from probe.resolver_alignments import JoinedAlignment
from probe.resolver_anchors import (
    AuditDirectReference,
    JoinedDirect,
)
from probe.resolver_assembly import (
    STAGE07_COLUMNS,
    resolve_joined_candidates,
)
from probe.resolver_candidates import CandidateProvenance, CandidateRecord
from probe.resolver_lineage import JoinedLineage

START = "2023_01"
END = "2026_03"
RELEASES = (START, END)


def candidate(
    release=START,
    target="T1",
    rid="1",
    *,
    lineage=False,
    fragment=False,
    accession="Q1",
    scope="PRIMARY",
    audit=None,
    go=True,
    rescue=False,
):
    return CandidateRecord(
        release=release,
        target_id=target,
        rid=rid,
        scope=scope,
        target_accession="P1",
        candidate_accession=accession,
        candidate_raw_ox="100",
        candidate_resolved_taxid="100",
        candidate_species_taxid="10",
        primary_bucket="S:10",
        fragment_record=fragment,
        discovery_channel="SEQUENCE_ONLY" if not lineage else "BOTH",
        sequence_discovered=go or rescue,
        go_positive_sequence_discovered=go,
        full_v2_rescue_sequence_discovered=rescue,
        lineage_discovered=lineage,
        lineage_key_count=int(lineage),
        lineage_collision_present=False,
        audit=audit,
    )


def anchor(*, match=None, eligible=False, promoted=False):
    return JoinedDirect(
        direct_match_type=match,
        direct_taxonomy_pass=eligible,
        audited_direct_verified=promoted,
        exact_resolved_taxid=not promoted,
        target_resolved_taxid="100",
        candidate_resolved_taxid="100",
        original_04b_key_source_eligible=eligible if match else None,
        taxonomy_relation="SAME_TAXON" if match else None,
    )


def locus(*, collision=False, term="CNAG_05404", accession=False):
    return JoinedLineage(
        accession_key_present=accession,
        collision_present=collision,
        matched_locus_keys=(MatchedLocusKey("ORF", term, True, True, collision, True),),
        matched_typed_keys=(("ORF", term),),
    )


def alignment(*, exact=True, partial=False, variant=False):
    return JoinedAlignment(
        aligned=True,
        exact_sequence=exact,
        target_length=10,
        candidate_length=8 if partial else 10,
        exact_subsequence=partial,
        equal_length_variant=variant,
        target_global=None,
        candidate_global=None,
    )


def _add(rows, alignments, record, direct, lineage, aln):
    rows[record.key] = CandidateProvenance(record, direct, lineage)
    alignments[record.key] = aln
    return record.key


def audit(reason="SAME_SPECIES_WRONG_SUBTAXON"):
    return AuditDirectReference(
        audit_class=(
            "CROSS_SPECIES_AUDIT"
            if reason == "CROSS_SPECIES"
            else "TAXONOMY_RESCUE_AUDIT"
        ),
        reason=reason,
        alignment_allowed=reason != "CROSS_SPECIES",
        direct_match_type="SECONDARY",
        direct_match_accession="P1",
        taxonomy_relation=(
            "CROSS_SPECIES"
            if reason == "CROSS_SPECIES"
            else "SAME_SPECIES_DIFFERENT_SUBTAXON"
        ),
        target_resolved_taxid="100",
        anchor_resolved_taxid="200",
    )


def audited_record(release=END, target="T1", rid="10", *, cross=False):
    record = candidate(
        release,
        target,
        rid,
        scope="TAXONOMY_AUDIT",
        audit=audit("CROSS_SPECIES" if cross else "SAME_SPECIES_WRONG_SUBTAXON"),
        go=False,
        rescue=False,
        accession="Q99",
    )
    record = replace(
        record,
        discovery_channel=None,
        candidate_resolved_taxid="200",
    )
    facts = JoinedDirect(
        direct_match_type="SECONDARY",
        direct_taxonomy_pass=False,
        audited_direct_verified=not cross,
        exact_resolved_taxid=False,
        target_resolved_taxid="100",
        candidate_resolved_taxid="200",
        original_04b_key_source_eligible=False,
        taxonomy_relation=(
            "CROSS_SPECIES" if cross else "SAME_SPECIES_DIFFERENT_SUBTAXON"
        ),
    )
    aln = (
        alignment()
        if not cross
        else JoinedAlignment(
            aligned=False,
            exact_sequence=None,
            target_length=None,
            candidate_length=None,
            exact_subsequence=False,
            equal_length_variant=False,
            target_global=None,
            candidate_global=None,
        )
    )
    return record, facts, aln


def universe():
    rows = {}
    alignments = {}
    direct = candidate(START, "T1", "20", accession="P1")
    direct_key = _add(
        rows,
        alignments,
        direct,
        anchor(match="PRIMARY", eligible=True),
        None,
        alignment(),
    )
    partial = candidate(
        START,
        "T1",
        "3",
        lineage=True,
        fragment=True,
    )
    partial_key = _add(
        rows,
        alignments,
        partial,
        anchor(),
        locus(),
        alignment(exact=False, partial=True),
    )
    weak = candidate(START, "T1", "2", lineage=True)
    weak_key = _add(
        rows,
        alignments,
        weak,
        anchor(),
        locus(term="SO:0001217"),
        alignment(),
    )
    rescue = candidate(
        END,
        "T2",
        "6",
        go=False,
        rescue=True,
    )
    rescue_key = _add(
        rows,
        alignments,
        rescue,
        anchor(),
        None,
        alignment(exact=False, partial=True),
    )
    same, same_direct, same_aln = audited_record()
    audit_key = _add(
        rows,
        alignments,
        same,
        same_direct,
        None,
        same_aln,
    )
    cross, cross_direct, cross_aln = audited_record(
        END,
        "T2",
        "10",
        cross=True,
    )
    cross_key = _add(
        rows,
        alignments,
        cross,
        cross_direct,
        None,
        cross_aln,
    )
    return (
        rows,
        alignments,
        {
            "direct": direct_key,
            "partial": partial_key,
            "weak": weak_key,
            "rescue": rescue_key,
            "audit": audit_key,
            "cross": cross_key,
        },
    )


def run(rows, alignments, policy=None):
    return resolve_joined_candidates(
        provenance=rows,
        alignments=alignments,
        release_order=RELEASES,
        policy=policy,
    )


def test_strict_preserves_entire_candidate_universe():
    rows, alignments, keys = universe()
    resolved = run(rows, alignments)
    assert set(resolved) == set(rows) == set(alignments)
    assert len(resolved) == 6
    assert resolved[keys["direct"]].decision.decision_basis == (
        "DIRECT_PRIMARY_ACCESSION"
    )
    assert resolved[keys["partial"]].decision.decision_basis == (
        "STABLE_UNIQUE_LOCUS_EXACT_SUBSEQUENCE"
    )
    assert resolved[keys["weak"]].decision.decision_basis == (
        "WEAK_OR_COLLIDING_DOCUMENTARY_PLUS_EXACT_SEQUENCE"
    )
    assert resolved[keys["rescue"]].decision.decision_basis == "SEQUENCE_ONLY"
    assert resolved[keys["audit"]].decision.decision == "AUDIT_ONLY"
    assert resolved[keys["cross"]].decision.decision == "AUDIT_ONLY"
    assert list(resolved) == [
        keys["weak"],
        keys["partial"],
        keys["direct"],
        keys["audit"],
        keys["rescue"],
        keys["cross"],
    ]


def test_multiplicity_and_warning_eligibility():
    rows, alignments, keys = universe()
    result = run(rows, alignments)
    for name in ("direct", "partial", "weak"):
        assert result[keys[name]].decision.accepted_candidates_for_target_release == 2
    for name in ("direct", "partial"):
        assert "MULTIPLE_ACCEPTED_CANDIDATES" in (result[keys[name]].decision.warnings)
    assert "MULTIPLE_ACCEPTED_CANDIDATES" not in (
        result[keys["weak"]].decision.warnings
    )
    assert result[keys["partial"]].decision.annotation_donor
    assert not result[keys["weak"]].decision.annotation_donor


def test_full_rescue_subsequence_is_descriptive_only():
    rows, alignments, keys = universe()
    result = run(rows, alignments)
    r = result[keys["rescue"]]
    assert r.decision.decision == "UNRESOLVED"
    assert r.sequence_relation == "CANDIDATE_EXACT_CONTIGUOUS_SUBSEQUENCE"


def test_generic_ontology_key_cannot_accept_exact_sequence():
    rows, alignments, keys = universe()
    r = run(rows, alignments)[keys["weak"]]
    assert r.decision.decision == "UNRESOLVED"
    assert r.sequence_relation == "EXACT_SEQUENCE"


def test_documentary_fragment_report_only_does_not_change_decision():
    rows, alignments, keys = universe()
    report = run(
        rows,
        alignments,
        ResolverPolicy(documentary_fragment="report_only"),
    )
    assert report[keys["partial"]].decision.decision == "ACCEPTED"
    assert not report[keys["partial"]].decision.annotation_donor
    assert "FRAGMENT_RECORD" in report[keys["partial"]].decision.warnings


def test_partial_sequence_report_only_does_not_change_decision():
    rows, alignments, keys = universe()
    report = run(
        rows,
        alignments,
        ResolverPolicy(partial_sequence="report_only"),
    )
    assert report[keys["partial"]].decision.decision == "ACCEPTED"
    assert not report[keys["partial"]].decision.annotation_donor
    assert report[keys["direct"]].decision.annotation_donor


def test_audit_promotion_keeps_original_scope():
    rows, alignments, keys = universe()
    promote = run(
        rows,
        alignments,
        ResolverPolicy(subtaxon_policy="include_audited_same_species"),
    )
    r = promote[keys["audit"]]
    assert r.decision.decision == "ACCEPTED"
    assert r.decision.decision_basis == "DIRECT_SECONDARY_ACCESSION"
    assert r.decision.source_scope == "TAXONOMY_AUDIT"
    assert r.decision.evaluation_scope == "PRIMARY"
    assert r.tsv_row()["scope"] == "TAXONOMY_AUDIT"
    assert r.tsv_row()["audit_outcome"] == ("EVALUATED_UNDER_AUDITED_SUBTAXON_POLICY")
    assert promote[keys["cross"]].decision.decision == "AUDIT_ONLY"
    assert promote[keys["cross"]].tsv_row()["audit_outcome"] == (
        "REJECTED_BY_TAXONOMY_POLICY"
    )


def test_tsv_schema_and_audit_nulls():
    rows, alignments, keys = universe()
    resolved = run(rows, alignments)
    primary = resolved[keys["direct"]].tsv_row()
    assert tuple(primary) == STAGE07_COLUMNS
    assert len(primary) == 19
    assert primary["scope"] == primary["evaluation_scope"] == "PRIMARY"
    assert primary["audit_class"] == "."
    assert primary["audit_outcome"] == "."
    assert primary["annotation_donor"] == "YES"
    weak = resolved[keys["weak"]].tsv_row()
    assert weak["annotation_donor"] == "NO"
    assert weak["warnings"] != "."
    cross = resolved[keys["cross"]].tsv_row()
    assert cross["exact_sequence"] == "."
    assert cross["sequence_relation"] == "NOT_ALIGNED_BY_POLICY"
    assert cross["audit_alignment_allowed"] == "0"
    assert cross["audit_reason"] == "CROSS_SPECIES"


def test_independent_input_order_gives_same_output():
    rows, alignments, _ = universe()
    first = run(rows, alignments)
    reversed_rows = dict(reversed(list(rows.items())))
    reversed_aln = dict(reversed(list(alignments.items())))
    second = run(reversed_rows, reversed_aln)
    assert [(k, v.tsv_row()) for k, v in first.items()] == [
        (k, v.tsv_row()) for k, v in second.items()
    ]


@pytest.mark.parametrize("which", ["missing", "extra"])
def test_alignment_key_coverage_fail_closed(which):
    rows, alignments, keys = universe()
    if which == "missing":
        del alignments[keys["weak"]]
    else:
        alignments[(END, "T3", "9")] = alignment()
    with pytest.raises(ValueError, match="alignment coverage mismatch"):
        run(rows, alignments)


def test_missing_documentary_join_fails_closed():
    rows, alignments, keys = universe()
    rows[keys["partial"]] = replace(rows[keys["partial"]], lineage=None)
    with pytest.raises(ValueError, match="missing 04D2"):
        run(rows, alignments)


def test_wrong_candidate_identity_fails_closed():
    rows, alignments, keys = universe()
    other = replace(rows[keys["weak"]].candidate, rid="500")
    rows[keys["weak"]] = replace(rows[keys["weak"]], candidate=other)
    with pytest.raises(ValueError, match="mismatched candidate provenance"):
        run(rows, alignments)


def test_unverified_audit_promotion_fails_closed():
    rows, alignments, keys = universe()
    direct = replace(rows[keys["audit"]].direct, audited_direct_verified=False)
    rows[keys["audit"]] = replace(rows[keys["audit"]], direct=direct)
    with pytest.raises(ValueError, match="unverified audited direct anchor"):
        run(rows, alignments)


def test_cross_species_alignment_contradiction_fails_closed():
    rows, alignments, keys = universe()
    alignments[keys["cross"]] = alignment()
    with pytest.raises(ValueError, match="audit alignment policy mismatch"):
        run(rows, alignments)


def test_invalid_release_or_duplicate_release_fails_closed():
    rows, alignments, _ = universe()
    with pytest.raises(ValueError, match="two distinct"):
        resolve_joined_candidates(
            provenance=rows,
            alignments=alignments,
            release_order=(START, START),
        )
    with pytest.raises(ValueError, match="Unexpected Stage 07 release"):
        resolve_joined_candidates(
            provenance=rows,
            alignments=alignments,
            release_order=("new", END),
        )


def test_nonidentifying_locus_cannot_override_direct_continuity():
    rows, alignments, keys = universe()
    original = rows[keys["weak"]]
    rows[keys["weak"]] = replace(
        original,
        direct=anchor(match="SECONDARY", eligible=True),
    )
    r = run(rows, alignments)[keys["weak"]]
    assert r.decision.decision == "ACCEPTED"
    assert r.decision.decision_basis == "DIRECT_SECONDARY_ACCESSION"


def test_exact_nondirect_documentary_accession_is_accepted():
    rows, alignments, keys = universe()
    key = keys["weak"]
    old = rows[key]
    typed = JoinedLineage(
        accession_key_present=True,
        collision_present=False,
        matched_locus_keys=(),
        matched_typed_keys=(("ACCESSION", "P1"),),
    )
    rows[key] = replace(old, lineage=typed)
    result = run(rows, alignments)[key]
    assert result.decision.decision == "ACCEPTED"
    assert result.decision.decision_basis == (
        "DOCUMENTARY_ACCESSION_PLUS_EXACT_SEQUENCE"
    )
    assert result.decision.annotation_donor


def test_nonexact_nondirect_accession_does_not_mask_stable_locus():
    rows, alignments, keys = universe()
    key = keys["partial"]
    old = rows[key]
    typed = JoinedLineage(
        accession_key_present=True,
        collision_present=False,
        matched_locus_keys=old.lineage.matched_locus_keys,
        matched_typed_keys=(
            ("ACCESSION", "P1"),
            ("ORF", "CNAG_05404"),
        ),
    )
    rows[key] = replace(
        old,
        candidate=replace(old.candidate, lineage_key_count=2),
        lineage=typed,
    )
    result = run(rows, alignments)[key]
    assert result.decision.decision == "ACCEPTED"
    assert result.decision.decision_basis == "STABLE_UNIQUE_LOCUS_EXACT_SUBSEQUENCE"
    assert result.decision.annotation_donor


def test_equal_length_variant_requires_exact_resolved_taxid():
    rows, alignments, keys = universe()
    key = keys["partial"]
    alignments[key] = alignment(exact=False, variant=True)
    accepted = run(rows, alignments)[key]
    assert accepted.decision.decision_basis == (
        "STABLE_UNIQUE_LOCUS_EQUAL_LENGTH_VARIANT"
    )
    old = rows[key]
    rows[key] = replace(
        old,
        candidate=replace(old.candidate, candidate_resolved_taxid="200"),
        direct=replace(
            old.direct,
            candidate_resolved_taxid="200",
            exact_resolved_taxid=False,
        ),
    )
    unresolved = run(rows, alignments)[key]
    assert unresolved.decision.decision_basis == (
        "STABLE_UNIQUE_LOCUS_OTHER_SEQUENCE_RELATION"
    )


def test_colliding_locus_cannot_accept_nonexact_fragment():
    rows, alignments, keys = universe()
    key = keys["partial"]
    old = rows[key]
    rows[key] = replace(
        old,
        candidate=replace(old.candidate, lineage_collision_present=True),
        lineage=locus(collision=True),
    )
    result = run(rows, alignments)[key]
    assert result.decision.decision == "UNRESOLVED"
    assert result.decision.decision_basis == "WEAK_OR_COLLIDING_LINEAGE"
    assert "COLLIDING_DOCUMENTARY_KEY_PRESENT" in (result.decision.warnings)
    assert "FRAGMENT_RECORD" in result.decision.warnings


def test_taxid_boolean_disagreement_is_not_silently_used():
    rows, alignments, keys = universe()
    key = keys["partial"]
    old = rows[key]
    rows[key] = replace(
        old,
        direct=replace(old.direct, exact_resolved_taxid=False),
    )
    with pytest.raises(ValueError, match="contradictory exact resolved TaxID"):
        run(rows, alignments)


def test_audit_only_count_serialization():
    rows, alignments, keys = universe()
    resolved = run(rows, alignments)

    field = "accepted_candidates_for_target_release"

    for name in ("audit", "cross"):
        result = resolved[keys[name]]
        assert result.decision.decision == "AUDIT_ONLY"
        assert result.tsv_row()[field] == "."

    for name in ("direct", "partial", "weak"):
        result = resolved[keys[name]]
        assert result.tsv_row()[field] == "2"
