"""Versioned V2 metadata inheritance never becomes an unverified GO donor."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from probe.canonical_v2.documentary_handoff import EVIDENCE_FIELDS
from probe.canonical_v2.documentary_science import (
    bind_scientific_provenance,
    has_stable_documentary_locus,
    merge_documentary_facts,
    read_documentary_facts,
)
from probe.resolver import MatchedLocusKey
from probe.resolver_anchors import JoinedDirect
from probe.resolver_candidates import CandidateProvenance, CandidateRecord
from probe.resolver_lineage import JoinedLineage

RELEASES = {"START": "2023_01", "END": "2026_03"}
KEY = ("2023_01", "target-one", "7")


def _source(**changed: str) -> dict[str, str]:
    row = dict.fromkeys(EVIDENCE_FIELDS, "")
    row.update({
        "release": KEY[0],
        "target_id": KEY[1],
        "candidate_rid": KEY[2],
        "candidate_accession": "P12345",
        "candidate_raw_ox": "9606",
        "candidate_resolved_taxid": "9606",
        "target_resolved_taxid": "9606",
        "candidate_frag": "0",
        "candidate_sequence_sha256": "a" * 64,
        "source_kind": "EXACT",
        "exact_source_rid": "3",
        "exact_source_tax_scope": "SAME_TAXID",
        "source_roles": "EXACT_VERIFIED",
        "source_key_type": "ORDERED_LOCUS",
        "source_key_value": "LOC_7",
        "candidate_key_type": "ORDERED_LOCUS",
        "candidate_key_value": "LOC_7",
        "match_class": "LITERAL",
        "target_key_multiplicity": "1",
        "candidate_key_multiplicity": "1",
        "exact_origin_release": "2026_03",
        "exact_origin_target_taxid": "9606",
        "potential_unique_locus": "1",
    })
    row.update(changed)
    return row


def _write(path: Path, *rows: dict[str, str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(
            target, fieldnames=EVIDENCE_FIELDS,
            delimiter="\t", lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def _candidate() -> CandidateRecord:
    return CandidateRecord(
        release=KEY[0], target_id=KEY[1], rid=KEY[2],
        scope="PRIMARY", target_accession="", candidate_accession="P12345",
        candidate_raw_ox="9606", candidate_resolved_taxid="9606",
        candidate_species_taxid="9606", primary_bucket="SAME_TAXID",
        fragment_record=False, discovery_channel="CANONICAL_DOCUMENTARY_V2",
        sequence_discovered=False, go_positive_sequence_discovered=False,
        full_v2_rescue_sequence_discovered=False, lineage_discovered=False,
        lineage_key_count=0, lineage_collision_present=False, audit=None,
    )


def _direct() -> JoinedDirect:
    return JoinedDirect(
        direct_match_type=None, direct_taxonomy_pass=False,
        audited_direct_verified=False, exact_resolved_taxid=True,
        target_resolved_taxid="9606", candidate_resolved_taxid="9606",
        original_04b_key_source_eligible=None, taxonomy_relation=None,
    )


def test_inherited_exact_key_is_search_only(tmp_path: Path) -> None:
    source = tmp_path / "evidence.tsv"
    _write(source, _source())
    loaded = read_documentary_facts(path=source, releases=RELEASES)
    assert loaded[KEY].lineage_present
    assert not has_stable_documentary_locus(loaded[KEY])
    assert loaded[KEY].matched_locus_keys == ()


def test_independent_start_and_end_anchor_is_stable(tmp_path: Path) -> None:
    source = tmp_path / "evidence.tsv"
    _write(source, _source(
        source_kind="INDEPENDENT_04D1", exact_source_rid="",
        exact_source_tax_scope="", exact_origin_release="2023_01",
        source_roles="START_ANCHOR,END_ANCHOR",
    ))
    loaded = read_documentary_facts(path=source, releases=RELEASES)
    assert has_stable_documentary_locus(loaded[KEY])


def test_independent_single_anchor_is_weak(tmp_path: Path) -> None:
    source = tmp_path / "evidence.tsv"
    _write(source, _source(
        source_kind="INDEPENDENT_04D1", exact_source_rid="",
        exact_source_tax_scope="", exact_origin_release="2023_01",
        source_roles="START_ANCHOR",
    ))
    loaded = read_documentary_facts(path=source, releases=RELEASES)
    assert not has_stable_documentary_locus(loaded[KEY])


def test_normalized_hint_never_creates_locus(tmp_path: Path) -> None:
    source = tmp_path / "evidence.tsv"
    _write(source, _source(
        match_class="NORMALIZED_HINT", source_key_value="LOC-7",
        potential_unique_locus="0",
    ))
    loaded = read_documentary_facts(path=source, releases=RELEASES)
    assert loaded == {}


def test_cross_taxid_documentary_pair_rejected(tmp_path: Path) -> None:
    source = tmp_path / "evidence.tsv"
    _write(source, _source(candidate_resolved_taxid="9605"))
    with pytest.raises(ValueError, match="Cross-TaxID"):
        read_documentary_facts(path=source, releases=RELEASES)


def test_source_flag_must_match_recomputed_locus(tmp_path: Path) -> None:
    source = tmp_path / "evidence.tsv"
    _write(source, _source(potential_unique_locus="0"))
    with pytest.raises(ValueError, match="potential locus"):
        read_documentary_facts(path=source, releases=RELEASES)


def test_multiplicity_collision_cannot_be_promoted(tmp_path: Path) -> None:
    source = tmp_path / "evidence.tsv"
    _write(source, _source(
        target_key_multiplicity="2", potential_unique_locus="0",
    ))
    loaded = read_documentary_facts(path=source, releases=RELEASES)
    assert loaded[KEY].collision_present
    assert not has_stable_documentary_locus(loaded[KEY])


def test_old_verified_lineage_is_not_lost(tmp_path: Path) -> None:
    source = tmp_path / "evidence.tsv"
    _write(source, _source())
    fresh = read_documentary_facts(path=source, releases=RELEASES)
    original = JoinedLineage(
        accession_key_present=False, collision_present=False,
        matched_locus_keys=(MatchedLocusKey(
            key_type="ORF", key_value="ORF_A", from_start_anchor=True,
            from_end_anchor=True, key_collision=False, index_verified=True,
        ),),
        matched_typed_keys=(("ORF", "ORF_A"),),
    )
    merged = merge_documentary_facts(
        candidates={KEY: _candidate()},
        independently_checked={KEY: original}, new_evidence=fresh,
    )
    assert has_stable_documentary_locus(merged[KEY])
    assert len(merged[KEY].matched_typed_keys) == 2
    original_fact = CandidateProvenance(_candidate(), _direct(), original)
    bound = bind_scientific_provenance(
        candidate_facts={KEY: original_fact}, documentary_facts=merged,
    )
    assert bound[KEY].candidate.lineage_key_count == 2
    assert bound[KEY].candidate.lineage_discovered
    assert bound[KEY].direct == _direct()


def test_cannot_attach_documentary_to_foreign_pair() -> None:
    fact = CandidateProvenance(_candidate(), _direct(), None)
    foreign = ("2026_03", "target-one", "7")
    with pytest.raises(ValueError, match="unknown candidate"):
        bind_scientific_provenance(
            candidate_facts={KEY: fact},
            documentary_facts={foreign: JoinedLineage(
                accession_key_present=False, collision_present=False,
                matched_locus_keys=(), matched_typed_keys=(("ORF", "ORF_A"),),
            )},
        )


def test_merge_never_adds_candidate() -> None:
    source = JoinedLineage(
        accession_key_present=False, collision_present=False,
        matched_locus_keys=(), matched_typed_keys=(("ORF", "ORF_A"),),
    )
    with pytest.raises(ValueError, match="outside candidates"):
        merge_documentary_facts(
            candidates={}, independently_checked={}, new_evidence={KEY: source},
        )
