"""Synthetic 01/05A2/05C2 read-only candidate adapter contract tests."""

from __future__ import annotations

import csv
from dataclasses import replace

import pytest

from probe.resolver import MatchedLocusKey
from probe.resolver_candidates import (
    anchor_references,
    collect_candidate_provenance,
    lineage_references,
    load_candidate_universe,
    validate_lineage_coverage,
)
from probe.resolver_lineage import JoinedLineage

RELEASES = frozenset({"2023_01", "2026_03"})
TARGET_COLS = ("target_id", "raw_taxid", "optional_accession")
PRIMARY_COLS = (
    "release",
    "target_id",
    "rid",
    "frag",
    "discovery_channel",
    "sequence_discovered",
    "go_positive_sequence_discovered",
    "full_v2_rescue_sequence_discovered",
    "lineage_discovered",
    "target_primary_bucket",
    "candidate_primary_accession",
    "raw_ox",
    "resolved_taxid",
    "species_taxid",
    "lin_distinct_lineage_key_count",
    "lin_key_collision_present",
)
AUDIT_COLS = (
    "release",
    "target_id",
    "rid",
    "frag",
    "target_optional_accession",
    "target_raw_taxid",
    "target_resolved_taxid",
    "target_primary_bucket",
    "record_already_in_primary_union_for_target",
    "audit_class",
    "audit_alignment_allowed",
    "key_source_reason",
    "taxonomy_relation",
    "direct_match_type",
    "direct_match_accession",
    "candidate_primary_accession",
    "raw_ox",
    "anchor_resolved_taxid",
    "anchor_species_taxid",
    "sec",
)
PRIMARY_KEY = ("2023_01", "T1", "100")
AUDIT_KEY = ("2026_03", "T1", "202")
CROSS_KEY = ("2026_03", "T1", "203")


def target(**changes):
    row = dict(target_id="T1", raw_taxid="981087", optional_accession="P1")
    row.update(changes)
    return row


def primary(**changes):
    row = dict(
        release="2023_01",
        target_id="T1",
        rid="100",
        frag="0",
        discovery_channel="BOTH",
        sequence_discovered="1",
        go_positive_sequence_discovered="1",
        full_v2_rescue_sequence_discovered="0",
        lineage_discovered="1",
        target_primary_bucket="T:981087",
        candidate_primary_accession="P1",
        raw_ox="981087",
        resolved_taxid="981087",
        species_taxid="5661",
        lin_distinct_lineage_key_count="2",
        lin_key_collision_present="1",
    )
    row.update(changes)
    return row


def audit(**changes):
    row = dict(
        release="2026_03",
        target_id="T1",
        rid="202",
        frag="0",
        target_optional_accession="P1",
        target_raw_taxid="981087",
        target_resolved_taxid="981087",
        target_primary_bucket="T:981087",
        record_already_in_primary_union_for_target="0",
        audit_class="TAXONOMY_RESCUE_AUDIT",
        audit_alignment_allowed="1",
        key_source_reason="SAME_SPECIES_WRONG_SUBTAXON",
        taxonomy_relation="SAME_SPECIES_DIFFERENT_SUBTAXON",
        direct_match_type="SECONDARY",
        direct_match_accession="P1",
        candidate_primary_accession="Q2",
        raw_ox="5661",
        anchor_resolved_taxid="5661",
        anchor_species_taxid="5661",
        sec="P1,Q4",
    )
    row.update(changes)
    return row


def cross_species():
    return audit(
        rid="203",
        audit_class="CROSS_SPECIES_AUDIT",
        key_source_reason="CROSS_SPECIES",
        audit_alignment_allowed="0",
        taxonomy_relation="CROSS_SPECIES",
        raw_ox="9606",
        anchor_resolved_taxid="9606",
        anchor_species_taxid="9606",
    )


def write(path, columns, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=columns, delimiter="\t", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)
    return path


def load(
    tmp_path,
    *,
    target_rows=None,
    primary_rows=None,
    audit_rows=None,
    releases=RELEASES,
):
    if target_rows is None:
        target_rows = [target()]
    if primary_rows is None:
        primary_rows = [primary()]
    if audit_rows is None:
        audit_rows = [audit(), cross_species()]
    return load_candidate_universe(
        targets_path=write(tmp_path / "01.tsv", TARGET_COLS, target_rows),
        primary_path=write(tmp_path / "05a2.tsv", PRIMARY_COLS, primary_rows),
        audit_path=write(tmp_path / "05c2.tsv", AUDIT_COLS, audit_rows),
        release_ids=releases,
    )


def two_keys():
    return JoinedLineage(
        accession_key_present=True,
        collision_present=True,
        matched_locus_keys=(
            MatchedLocusKey("ORF", "LDBPK_251090", True, True, True, True),
        ),
        matched_typed_keys=(("ACCESSION", "P1"), ("ORF", "LDBPK_251090")),
    )


def test_preserves_primary_and_both_audit_scopes(tmp_path):
    rows = load(tmp_path)
    assert len(rows) == 3
    p, a, x = (rows[key] for key in (PRIMARY_KEY, AUDIT_KEY, CROSS_KEY))
    assert p.scope == "PRIMARY"
    assert p.target_accession == "P1"  # Stage 01, not inferred
    assert p.candidate_accession == "P1"
    assert p.lineage_discovered
    assert p.lineage_key_count == 2
    assert p.lineage_collision_present
    assert a.scope == "TAXONOMY_AUDIT"
    assert a.audit.reason == "SAME_SPECIES_WRONG_SUBTAXON"
    assert a.audit.alignment_allowed
    assert a.candidate_accession == "Q2"
    assert x.audit.reason == "CROSS_SPECIES"
    assert not x.audit.alignment_allowed


def test_references_match_existing_independent_adapters(tmp_path):
    rows = load(tmp_path)
    refs = anchor_references(rows)
    lineage = lineage_references(rows)
    assert set(refs) == {PRIMARY_KEY, AUDIT_KEY, CROSS_KEY}
    assert set(lineage) == {PRIMARY_KEY}
    assert refs[PRIMARY_KEY].audit is None
    assert refs[AUDIT_KEY].audit.direct_match_accession == "P1"
    assert refs[AUDIT_KEY].candidate_resolved_taxid == "5661"
    assert lineage[PRIMARY_KEY].candidate_accession == "P1"
    assert lineage[PRIMARY_KEY].primary_bucket == "T:981087"


def test_lineage_coverage_validates_aggregated_fields(tmp_path):
    rows = load(tmp_path)
    validate_lineage_coverage(rows, {PRIMARY_KEY: two_keys()})
    with pytest.raises(ValueError, match="typed-key count"):
        mismatch = replace(two_keys(), matched_typed_keys=(("ORF", "X"),))
        validate_lineage_coverage(rows, {PRIMARY_KEY: mismatch})
    with pytest.raises(ValueError, match="collision disagreement"):
        mismatch = replace(two_keys(), collision_present=False)
        validate_lineage_coverage(rows, {PRIMARY_KEY: mismatch})
    with pytest.raises(ValueError, match="coverage mismatch"):
        validate_lineage_coverage(rows, {})
    with pytest.raises(ValueError, match="coverage mismatch"):
        validate_lineage_coverage(rows, {CROSS_KEY: two_keys()})


def test_sequence_only_go_positive_and_full_rescue_remain_separate(tmp_path):
    p = primary(
        discovery_channel="SEQUENCE_ONLY",
        lineage_discovered="0",
        lin_distinct_lineage_key_count="",
        lin_key_collision_present="",
    )
    rescue = primary(
        release="2026_03",
        rid="101",
        discovery_channel="FULL_V2_RESCUE_SEQUENCE_ONLY",
        go_positive_sequence_discovered="0",
        full_v2_rescue_sequence_discovered="1",
        lineage_discovered="0",
        lin_distinct_lineage_key_count="0",
        lin_key_collision_present="0",
        frag="1",
    )
    rows = load(tmp_path, primary_rows=[p, rescue], audit_rows=[])
    assert len(rows) == 2
    assert rows[("2026_03", "T1", "101")].fragment_record
    assert rows[("2026_03", "T1", "101")].full_v2_rescue_sequence_discovered
    assert not rows[("2026_03", "T1", "101")].lineage_discovered
    assert set(lineage_references(rows)) == set(rows)
    validate_lineage_coverage(rows, {})


def test_lineage_only_survives_without_sequence_discovery(tmp_path):
    p = primary(
        discovery_channel="LINEAGE_ONLY",
        sequence_discovered="0",
        go_positive_sequence_discovered="0",
    )
    rows = load(tmp_path, primary_rows=[p], audit_rows=[])
    assert rows[PRIMARY_KEY].lineage_discovered
    assert not rows[PRIMARY_KEY].sequence_discovered


def test_missing_target_accession_preserved_not_synthesized(tmp_path):
    p = primary(candidate_primary_accession="X9")
    rows = load(
        tmp_path,
        target_rows=[target(optional_accession="")],
        primary_rows=[p],
        audit_rows=[],
    )
    assert rows[PRIMARY_KEY].target_accession == ""


@pytest.mark.parametrize(
    "bad,pattern",
    [
        ({"sequence_discovered": "0"}, "discovery provenance"),
        ({"go_positive_sequence_discovered": "2"}, "binary flag"),
        ({"lineage_discovered": "2"}, "binary flag"),
        ({"frag": "2"}, "binary flag"),
        ({"lin_distinct_lineage_key_count": "oops"}, "positive integer"),
        ({"lin_distinct_lineage_key_count": "0"}, "nonpositive count"),
        ({"lin_key_collision_present": "oops"}, "binary flag"),
        ({"candidate_primary_accession": ""}, "candidate metadata"),
        ({"release": "unknown"}, "unexpected release"),
    ],
)
def test_primary_invalid_metadata_fails_closed(tmp_path, bad, pattern):
    with pytest.raises(ValueError, match=pattern):
        load(tmp_path, primary_rows=[primary(**bad)], audit_rows=[])


@pytest.mark.parametrize(
    "bad,pattern",
    [
        ({"target_raw_taxid": "9606"}, "target TaxID mismatch"),
        ({"target_optional_accession": "X"}, "target accession mismatch"),
        ({"audit_alignment_allowed": "2"}, "binary flag"),
        ({"audit_class": "CROSS_SPECIES_AUDIT"}, "Contradictory"),
        ({"record_already_in_primary_union_for_target": "1"}, "already in PRIMARY"),
        ({"direct_match_type": "OTHER"}, "direct match type"),
        ({"direct_match_accession": "X"}, "SECONDARY accession mismatch"),
        ({"candidate_primary_accession": ""}, "candidate metadata"),
        ({"frag": "2"}, "binary flag"),
    ],
)
def test_audit_invalid_provenance_fails_closed(tmp_path, bad, pattern):
    with pytest.raises(ValueError, match=pattern):
        load(tmp_path, primary_rows=[], audit_rows=[audit(**bad)])


def test_duplicate_primary_key_rejected(tmp_path):
    with pytest.raises(ValueError, match="duplicate"):
        load(tmp_path, primary_rows=[primary(), primary()], audit_rows=[])


def test_duplicate_audit_key_rejected(tmp_path):
    with pytest.raises(ValueError, match="duplicate"):
        load(tmp_path, primary_rows=[], audit_rows=[audit(), audit()])


def test_primary_audit_overlap_rejected(tmp_path):
    audited = audit(release="2023_01", rid="100")
    with pytest.raises(ValueError, match="PRIMARY/audit overlap"):
        load(tmp_path, audit_rows=[audited])


def test_missing_target_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="unknown Stage 01 target"):
        load(tmp_path, target_rows=[], audit_rows=[])


def test_duplicate_target_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="duplicate Stage 01 target"):
        load(tmp_path, target_rows=[target(), target()], audit_rows=[])


def test_release_configuration_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="Expected two distinct"):
        load(tmp_path, releases=frozenset({"2023_01"}))


def test_missing_required_column_fails_closed(tmp_path):
    target_path = write(tmp_path / "01.tsv", TARGET_COLS, [target()])
    primary_path = write(tmp_path / "05a2.tsv", PRIMARY_COLS[:-1], [])
    audit_path = write(tmp_path / "05c2.tsv", AUDIT_COLS, [])
    with pytest.raises(ValueError, match="missing columns"):
        load_candidate_universe(
            targets_path=target_path,
            primary_path=primary_path,
            audit_path=audit_path,
            release_ids=RELEASES,
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
ANCHOR_COLS = (
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
INDEX_COLS = (
    "release",
    "target_id",
    "primary_bucket",
    "key_type",
    "key_value",
    "key_target_multiplicity",
    "key_collision",
    "from_target_accession",
    "from_start_anchor",
    "from_end_anchor",
    "source_roles",
)
EVIDENCE_COLS = (
    "release",
    "target_id",
    "rid",
    "target_primary_bucket",
    "key_type",
    "key_value",
    "key_target_multiplicity",
    "key_collision",
    "from_target_accession",
    "from_start_anchor",
    "from_end_anchor",
    "source_roles",
    "candidate_primary_accession",
)


def anchor_row(release, rid, *, audited=False, cross=False):
    row = dict(
        release=release,
        target_id="T1",
        rid=rid,
        target_optional_accession="P1",
        direct_match_type="PRIMARY",
        direct_match_accession="P1",
        primary_accession="P1",
        raw_ox="981087",
        sec="",
        target_raw_taxid="981087",
        target_resolved_taxid="981087",
        target_resolved_rank="strain",
        target_species_taxid="5661",
        target_primary_bucket="T:981087",
        anchor_resolution_status="CURRENT",
        anchor_resolved_taxid="981087",
        anchor_species_taxid="5661",
        taxonomy_relation="SAME_TAXON",
        key_source_eligible="1",
        key_source_reason="ELIGIBLE_SAME_SPECIES",
    )
    if audited:
        row.update(
            direct_match_type="SECONDARY",
            primary_accession="Q2",
            sec="P1,Q4",
            raw_ox="5661",
            anchor_resolved_taxid="5661",
            taxonomy_relation="SAME_SPECIES_DIFFERENT_SUBTAXON",
            key_source_eligible="0",
            key_source_reason="SAME_SPECIES_WRONG_SUBTAXON",
        )
    if cross:
        row.update(
            primary_accession="Q2",
            raw_ox="9606",
            sec="P1,Q4",
            anchor_resolved_taxid="9606",
            anchor_species_taxid="9606",
            direct_match_type="SECONDARY",
            taxonomy_relation="CROSS_SPECIES",
            key_source_eligible="0",
            key_source_reason="CROSS_SPECIES",
        )
    return row


def join_all(tmp_path, candidates, *, end_anchor=True):
    tax_row = dict(
        target_id="T1",
        raw_taxid="981087",
        resolution_status="CURRENT",
        resolved_taxid="981087",
        resolved_rank="strain",
        species_taxid="5661",
        primary_bucket="T:981087",
    )
    taxonomy_paths = {}
    anchor_paths = {}
    evidence_paths = {}
    for release in sorted(RELEASES):
        taxonomy_paths[release] = write(
            tmp_path / f"{release}_02b.tsv", TAX_COLS, [tax_row]
        )
        if release == "2023_01":
            anchors = [anchor_row(release, "100")]
        else:
            anchors = [anchor_row(release, "202", audited=True)]
            if end_anchor:
                anchors.append(anchor_row(release, "203", cross=True))
        anchor_paths[release] = write(
            tmp_path / f"{release}_04b.tsv", ANCHOR_COLS, anchors
        )
        evidence_paths[release] = write(
            tmp_path / f"{release}_04d2.tsv", EVIDENCE_COLS, []
        )
    return collect_candidate_provenance(
        candidates=candidates,
        target_taxonomy_paths=taxonomy_paths,
        classified_anchor_paths=anchor_paths,
        index_path=write(tmp_path / "04d1.tsv", INDEX_COLS, []),
        evidence_paths=evidence_paths,
    )


def test_integrates_four_independent_stages_without_promotion(tmp_path):
    p = primary(
        discovery_channel="SEQUENCE_ONLY",
        lineage_discovered="0",
        lin_distinct_lineage_key_count="",
        lin_key_collision_present="",
    )
    candidates = load(tmp_path, primary_rows=[p])
    joined = join_all(tmp_path, candidates)
    assert set(joined) == {PRIMARY_KEY, AUDIT_KEY, CROSS_KEY}
    assert joined[PRIMARY_KEY].direct.direct_match_type == "PRIMARY"
    assert joined[PRIMARY_KEY].direct.direct_taxonomy_pass
    assert joined[PRIMARY_KEY].lineage is None
    assert joined[AUDIT_KEY].direct.audited_direct_verified
    assert not joined[AUDIT_KEY].direct.exact_resolved_taxid
    assert joined[AUDIT_KEY].candidate.scope == "TAXONOMY_AUDIT"
    assert not joined[CROSS_KEY].direct.audited_direct_verified
    assert joined[CROSS_KEY].candidate.audit.reason == "CROSS_SPECIES"


def test_integrated_audit_without_04b_fails_closed(tmp_path):
    p = primary(
        discovery_channel="SEQUENCE_ONLY",
        lineage_discovered="0",
        lin_distinct_lineage_key_count="",
        lin_key_collision_present="",
    )
    candidates = load(tmp_path, primary_rows=[p])
    with pytest.raises(ValueError, match="lacks independently verified 04B"):
        join_all(tmp_path, candidates, end_anchor=False)


def test_bad_composite_lineage_coverage_is_detected(tmp_path):
    candidates = load(tmp_path)
    with pytest.raises(ValueError, match="coverage mismatch"):
        join_all(tmp_path, candidates)


def test_audit_to_primary_overlap_is_preserved_not_duplicated(tmp_path):
    audit_row = audit(
        release="2023_01", rid="100",
        target_resolved_taxid="981087",
        record_already_in_primary_union_for_target="1",
        candidate_primary_accession="P1", raw_ox="981087",
        anchor_resolved_taxid="989999", anchor_species_taxid="5661",
        direct_match_type="PRIMARY", direct_match_accession="P1",
        taxonomy_relation="SAME_SPECIES_DIFFERENT_SUBTAXON",
    )
    p = primary(resolved_taxid="989999")
    rows = load(tmp_path, primary_rows=[p], audit_rows=[audit_row])
    ref = rows[PRIMARY_KEY]
    assert len(rows) == 1
    assert ref.scope == "PRIMARY"
    assert ref.audit is None
    assert ref.overlap_audit is not None
    assert ref.overlap_audit.alignment_allowed
    assert ref.anchor_reference().primary_audit_overlap


def test_audit_to_primary_overlap_identity_conflict_rejected(tmp_path):
    audit_row = audit(
        release="2023_01", rid="100",
        record_already_in_primary_union_for_target="1",
        candidate_primary_accession="BAD",
    )
    with pytest.raises(ValueError, match="overlap audit identity mismatch"):
        load(tmp_path, audit_rows=[audit_row])


def test_stage2c_full_exact_is_distinct_from_legacy_rescue(tmp_path):
    row = primary(
        discovery_channel="STAGE2C_FULL_EXACT",
        go_positive_sequence_discovered="0",
        full_v2_rescue_sequence_discovered="0",
        lineage_discovered="0",
        lin_distinct_lineage_key_count="",
        lin_key_collision_present="",
    )
    candidate = load(tmp_path, primary_rows=[row], audit_rows=[])[PRIMARY_KEY]
    assert candidate.scope == "PRIMARY"
    assert candidate.discovery_channel == "STAGE2C_FULL_EXACT"
    assert candidate.sequence_discovered
    assert not candidate.go_positive_sequence_discovered
    assert not candidate.full_v2_rescue_sequence_discovered
    assert not candidate.lineage_discovered


@pytest.mark.parametrize(
    "changes",
    [
        {"discovery_channel": "SEQUENCE_ONLY"},
        {"sequence_discovered": "0"},
        {"go_positive_sequence_discovered": "1"},
        {"full_v2_rescue_sequence_discovered": "1"},
    ],
)
def test_stage2c_full_exact_provenance_fail_closed(tmp_path, changes):
    row = primary(
        discovery_channel="STAGE2C_FULL_EXACT",
        go_positive_sequence_discovered="0",
        full_v2_rescue_sequence_discovered="0",
        lineage_discovered="0",
        lin_distinct_lineage_key_count="",
        lin_key_collision_present="",
    )
    row.update(changes)
    with pytest.raises(ValueError, match="inconsistent discovery provenance"):
        load(tmp_path, primary_rows=[row], audit_rows=[])


def test_stage2c_go_and_full_preserves_both_sources(tmp_path):
    row = primary(
        discovery_channel="STAGE2C_GO_AND_FULL",
        go_positive_sequence_discovered="1",
        full_v2_rescue_sequence_discovered="0",
        lineage_discovered="0",
        lin_distinct_lineage_key_count="",
        lin_key_collision_present="",
    )
    candidate = load(tmp_path, primary_rows=[row], audit_rows=[])[PRIMARY_KEY]
    assert candidate.discovery_channel == "STAGE2C_GO_AND_FULL"
    assert candidate.go_positive_sequence_discovered
    assert not candidate.full_v2_rescue_sequence_discovered
