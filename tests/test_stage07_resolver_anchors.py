"""Synthetic Stage 07 02B/04B exact-provenance and taxonomy adapter checks."""

from __future__ import annotations

import csv
from dataclasses import replace

import pytest

from probe.resolver import ResolverEvidence, ResolverPolicy, decide
from probe.resolver_anchors import (
    AuditDirectReference,
    CandidateAnchorReference,
    collect_direct_anchors,
)

TAX_COLS = (
    "target_id",
    "raw_taxid",
    "resolution_status",
    "resolved_taxid",
    "resolved_rank",
    "species_taxid",
    "primary_bucket",
)
B_COLS = (
    "release",
    "target_id",
    "rid",
    "target_optional_accession",
    "direct_match_type",
    "direct_match_accession",
    "primary_accession",
    "raw_ox",
    "sec",
    "target_raw_taxid",
    "target_resolved_taxid",
    "target_resolved_rank",
    "target_species_taxid",
    "target_primary_bucket",
    "anchor_resolution_status",
    "anchor_resolved_taxid",
    "anchor_species_taxid",
    "taxonomy_relation",
    "key_source_eligible",
    "key_source_reason",
)
KEY = ("2023_01", "T1", "259")


def tax(**updates):
    result = dict(
        target_id="T1",
        raw_taxid="7227",
        resolution_status="CURRENT",
        resolved_taxid="7227",
        resolved_rank="species",
        species_taxid="7227",
        primary_bucket="S:7227",
    )
    result.update(updates)
    return result


def anchor(**updates):
    result = dict(
        release="2023_01",
        target_id="T1",
        rid="259",
        target_optional_accession="P32234",
        direct_match_type="PRIMARY",
        direct_match_accession="P32234",
        primary_accession="P32234",
        raw_ox="7227",
        sec="Q9V648",
        target_raw_taxid="7227",
        target_resolved_taxid="7227",
        target_resolved_rank="species",
        target_species_taxid="7227",
        target_primary_bucket="S:7227",
        anchor_resolution_status="CURRENT",
        anchor_resolved_taxid="7227",
        anchor_species_taxid="7227",
        taxonomy_relation="SAME_TAXON",
        key_source_eligible="1",
        key_source_reason="ELIGIBLE_SAME_SPECIES",
    )
    result.update(updates)
    return result


def ref(**updates):
    result = dict(
        scope="PRIMARY",
        target_accession="P32234",
        candidate_accession="P32234",
        candidate_raw_ox="7227",
        candidate_resolved_taxid="7227",
        candidate_species_taxid="7227",
        primary_bucket="S:7227",
    )
    result.update(updates)
    return CandidateAnchorReference(**result)


def audit_setup(*, cross_species=False):
    is_cross = cross_species
    reason = "CROSS_SPECIES" if is_cross else "SAME_SPECIES_WRONG_SUBTAXON"
    relation = "CROSS_SPECIES" if is_cross else "SAME_SPECIES_DIFFERENT_SUBTAXON"
    species = "9606" if is_cross else "5661"
    a = AuditDirectReference(
        audit_class="CROSS_SPECIES_AUDIT" if is_cross else "TAXONOMY_RESCUE_AUDIT",
        reason=reason,
        alignment_allowed=not is_cross,
        direct_match_type="SECONDARY",
        direct_match_accession="E9BHL3",
        taxonomy_relation=relation,
        target_resolved_taxid="981087",
        anchor_resolved_taxid=species,
    )
    t = tax(
        raw_taxid="981087",
        resolved_taxid="981087",
        resolved_rank="strain",
        species_taxid="5661",
        primary_bucket="T:981087",
    )
    b = anchor(
        target_optional_accession="E9BHL3",
        direct_match_type="SECONDARY",
        direct_match_accession="E9BHL3",
        primary_accession="I7HD01",
        sec="E9BHL3",
        raw_ox=species,
        target_raw_taxid="981087",
        target_resolved_taxid="981087",
        target_resolved_rank="strain",
        target_species_taxid="5661",
        target_primary_bucket="T:981087",
        anchor_resolved_taxid=species,
        anchor_species_taxid=species,
        taxonomy_relation=relation,
        key_source_eligible="0",
        key_source_reason=reason,
    )
    c = ref(
        scope="TAXONOMY_AUDIT",
        target_accession="E9BHL3",
        candidate_accession="I7HD01",
        candidate_raw_ox=species,
        candidate_resolved_taxid=species,
        candidate_species_taxid=species,
        primary_bucket="T:981087",
        audit=a,
    )
    return t, b, c


def write(path, columns, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        w = csv.DictWriter(
            handle, fieldnames=columns, delimiter="\t", lineterminator="\n"
        )
        w.writeheader()
        w.writerows(rows)
    return path


def collect(tmp_path, *, target_rows=None, anchor_rows=None, refs=None, releases=None):
    if target_rows is None:
        target_rows = [tax()]
    if anchor_rows is None:
        anchor_rows = [anchor()]
    if refs is None:
        refs = {KEY: ref()}
    if releases is None:
        releases = {"2023_01": (target_rows, anchor_rows)}
    tax_paths = {}
    anchor_paths = {}
    for release, (t_rows, b_rows) in releases.items():
        tax_paths[release] = write(tmp_path / f"{release}_02b.tsv", TAX_COLS, t_rows)
        anchor_paths[release] = write(tmp_path / f"{release}_04b.tsv", B_COLS, b_rows)
    return collect_direct_anchors(
        target_taxonomy_paths=tax_paths,
        classified_anchor_paths=anchor_paths,
        candidates=refs,
    )


def to_evidence(
    joined, *, scope="PRIMARY", exact=True, audit_reason=None, permitted=None
):
    return ResolverEvidence(
        scope=scope,
        exact_sequence=exact,
        audit_reason=audit_reason,
        audit_alignment_allowed=permitted,
        direct_match_type=joined.direct_match_type,
        direct_taxonomy_pass=joined.direct_taxonomy_pass,
        audited_direct_verified=joined.audited_direct_verified,
        exact_resolved_taxid=joined.exact_resolved_taxid,
    )


def test_primary_direct_anchor_and_kernel_acceptance(tmp_path):
    x = collect(tmp_path)[KEY]
    assert x.direct_match_type == "PRIMARY"
    assert x.direct_taxonomy_pass
    assert x.exact_resolved_taxid
    assert not x.audited_direct_verified
    assert decide(to_evidence(x)).decision_basis == "DIRECT_PRIMARY_ACCESSION"


def test_secondary_direct_anchor_is_verified_from_secondary_field(tmp_path):
    b = anchor(
        direct_match_type="SECONDARY",
        direct_match_accession="P32234",
        primary_accession="Q9OTHER",
        sec="P32234",
    )
    c = ref(candidate_accession="Q9OTHER")
    x = collect(tmp_path, anchor_rows=[b], refs={KEY: c})[KEY]
    assert x.direct_taxonomy_pass
    assert decide(to_evidence(x)).decision_basis == "DIRECT_SECONDARY_ACCESSION"


def test_sequence_changed_direct_is_still_accepted(tmp_path):
    x = collect(tmp_path)[KEY]
    d = decide(to_evidence(x, exact=False))
    assert d.decision == "ACCEPTED"
    assert d.decision_basis == "DIRECT_ACCESSION_CONTINUITY"


def test_no_04b_row_does_not_invent_direct_from_equal_accessions(tmp_path):
    x = collect(tmp_path, anchor_rows=[])[KEY]
    assert x.direct_match_type is None
    assert x.original_04b_key_source_eligible is None
    assert x.exact_resolved_taxid
    assert decide(to_evidence(x)).decision_basis == "EXACT_SEQUENCE_ONLY"


def test_strict_04b_ineligible_is_not_promoted(tmp_path):
    x = collect(tmp_path, anchor_rows=[anchor(key_source_eligible="0")])[KEY]
    assert x.direct_match_type == "PRIMARY"
    assert not x.direct_taxonomy_pass
    assert not x.audited_direct_verified
    assert decide(to_evidence(x)).decision == "UNRESOLVED"


def test_optional_subtaxon_policy_only_uses_verified_05c2_and_04b(tmp_path):
    t, b, c = audit_setup()
    x = collect(tmp_path, target_rows=[t], anchor_rows=[b], refs={KEY: c})[KEY]
    assert x.direct_match_type == "SECONDARY"
    assert not x.direct_taxonomy_pass
    assert x.audited_direct_verified
    assert not x.exact_resolved_taxid  # same species is NOT same resolved TaxID
    ev = to_evidence(
        x, scope="TAXONOMY_AUDIT", audit_reason=c.audit.reason, permitted=True
    )
    default = decide(ev)
    assert default.decision == "AUDIT_ONLY"
    promoted = decide(
        ev, ResolverPolicy(subtaxon_policy="include_audited_same_species")
    )
    assert promoted.decision == "ACCEPTED"
    assert promoted.decision_basis == "DIRECT_SECONDARY_ACCESSION"
    assert promoted.source_scope == "TAXONOMY_AUDIT"
    assert promoted.evaluation_scope == "PRIMARY"


def test_cross_species_audit_never_promoted(tmp_path):
    t, b, c = audit_setup(cross_species=True)
    x = collect(tmp_path, target_rows=[t], anchor_rows=[b], refs={KEY: c})[KEY]
    assert not x.audited_direct_verified
    ev = to_evidence(
        x,
        scope="TAXONOMY_AUDIT",
        exact=None,
        audit_reason=c.audit.reason,
        permitted=False,
    )
    d = decide(ev, ResolverPolicy(subtaxon_policy="include_audited_same_species"))
    assert d.decision == "AUDIT_ONLY"
    assert d.audit_outcome == "REJECTED_BY_TAXONOMY_POLICY"


def test_taxid_different_even_for_same_species(tmp_path):
    t, b, c = audit_setup()
    x = collect(tmp_path, target_rows=[t], anchor_rows=[b], refs={KEY: c})[KEY]
    assert x.target_resolved_taxid == "981087"
    assert x.candidate_resolved_taxid == "5661"
    assert not x.exact_resolved_taxid


def test_primary_missing_target_accession_is_allowed_without_direct_anchor(tmp_path):
    x = collect(tmp_path, anchor_rows=[], refs={KEY: ref(target_accession="")})[KEY]
    assert x.direct_match_type is None


@pytest.mark.parametrize(
    "change,pattern",
    [
        ({"direct_match_accession": "P99999"}, "direct accession does not match"),
        ({"target_optional_accession": "Q99999"}, "target accession disagreement"),
        ({"primary_accession": "Q99999"}, "candidate accession mismatch"),
        ({"raw_ox": "9606"}, "candidate raw taxonomy mismatch"),
        ({"target_resolved_taxid": "10090"}, "04B/02B disagreement"),
        ({"target_primary_bucket": "S:10090"}, "04B/02B disagreement"),
        ({"anchor_resolved_taxid": "9606"}, "candidate resolved TaxID mismatch"),
        ({"anchor_species_taxid": "9606"}, "candidate species TaxID mismatch"),
        ({"key_source_eligible": "YES"}, "expected 0/1"),
        ({"direct_match_type": "ALIAS"}, "unknown 04B direct"),
        ({"anchor_resolution_status": "DELETED"}, "resolution status invalid"),
        ({"release": "2026_03"}, "release contradicts"),
    ],
)
def test_bad_04b_fails_closed(tmp_path, change, pattern):
    with pytest.raises(ValueError, match=pattern):
        collect(tmp_path, anchor_rows=[anchor(**change)])


@pytest.mark.parametrize(
    "modification,pattern",
    [
        (
            {
                "direct_match_type": "SECONDARY",
                "direct_match_accession": "P32234",
                "primary_accession": "Q99999",
                "sec": "Q00000",
            },
            "absent from 04B secondary",
        ),
    ],
)
def test_bad_secondary_provenance(tmp_path, modification, pattern):
    with pytest.raises(ValueError, match=pattern):
        collect(
            tmp_path,
            anchor_rows=[anchor(**modification)],
            refs={KEY: ref(candidate_accession="Q99999")},
        )


def test_audit_reason_mismatch_fails_closed(tmp_path):
    t, b, c = audit_setup()
    b["key_source_reason"] = "CROSS_SPECIES"
    with pytest.raises(ValueError, match="05C2/04B audit provenance disagreement"):
        collect(tmp_path, target_rows=[t], anchor_rows=[b], refs={KEY: c})


def test_audit_taxonomy_relation_mismatch_fails_closed(tmp_path):
    t, b, c = audit_setup()
    b["taxonomy_relation"] = "SAME_TAXON"
    with pytest.raises(ValueError, match="05C2/04B audit provenance disagreement"):
        collect(tmp_path, target_rows=[t], anchor_rows=[b], refs={KEY: c})


def test_same_species_audit_with_inconsistent_species_fails_closed(tmp_path):
    t, b, c = audit_setup()
    b["anchor_species_taxid"] = "9606"
    c = replace(c, candidate_species_taxid="9606")
    with pytest.raises(ValueError, match="inconsistent audited subtaxon override"):
        collect(tmp_path, target_rows=[t], anchor_rows=[b], refs={KEY: c})


def test_audit_cannot_be_silently_marked_04b_key_eligible(tmp_path):
    t, b, c = audit_setup()
    b["key_source_eligible"] = "1"
    with pytest.raises(ValueError, match="audited exclusion"):
        collect(tmp_path, target_rows=[t], anchor_rows=[b], refs={KEY: c})


def test_audit_without_04b_anchor_fails_closed(tmp_path):
    t, _, c = audit_setup()
    with pytest.raises(ValueError, match="lacks independently verified"):
        collect(tmp_path, target_rows=[t], anchor_rows=[], refs={KEY: c})


def test_missing_or_duplicate_02b_target_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="Missing 02B"):
        collect(tmp_path, target_rows=[])
    with pytest.raises(ValueError, match="duplicate 02B"):
        collect(tmp_path, target_rows=[tax(), tax()])


def test_duplicate_04b_anchor_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="multiple 04B"):
        collect(tmp_path, anchor_rows=[anchor(), anchor()])


def test_release_scoping(tmp_path):
    t, a = tax(), anchor()
    t2 = dict(t)
    a2 = dict(a, release="2026_03")
    refs = {KEY: ref(), ("2026_03", "T1", "259"): ref()}
    out = collect(
        tmp_path, refs=refs, releases={"2023_01": ([t], [a]), "2026_03": ([t2], [a2])}
    )
    assert len(out) == 2
    assert all(x.direct_taxonomy_pass for x in out.values())


def test_unresolved_taxid_never_counts_as_exact(tmp_path):
    t = tax(resolution_status="UNRESOLVED", resolved_taxid=".")
    x = collect(tmp_path, target_rows=[t], anchor_rows=[], refs={KEY: ref()})[KEY]
    assert not x.exact_resolved_taxid


def test_unknown_resolved_taxonomy_status_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="lacks verified resolution"):
        collect(tmp_path, target_rows=[tax(resolution_status="DELETED")])


def test_invalid_audit_reference_fails_closed():
    t, b, c = audit_setup()
    assert t and b and c
    with pytest.raises(ValueError, match="Contradictory 05C2"):
        replace(c.audit, alignment_allowed=False)


def test_non_direct_extra_04b_for_other_candidate_is_not_invented(tmp_path):
    b = anchor(rid="999", primary_accession="P99999")
    x = collect(tmp_path, anchor_rows=[b])[KEY]
    assert x.direct_match_type is None
