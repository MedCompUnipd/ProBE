"""Synthetic 04D1/04D2 keyed provenance tests for Stage 07 increment 2."""

import csv

import pytest

from probe.resolver import stable_unique_locus
from probe.resolver_lineage import CandidateReference, collect_lineage

D1 = (
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
D2 = (
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


def tsv(path, fields, rows):
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, delimiter="\t", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return path


def input_rows(**changes):
    common = dict(
        release="2023_01",
        target_id="T1",
        key_type="ORF",
        key_value="CG8340",
        key_target_multiplicity="1",
        key_collision="0",
        from_target_accession="0",
        from_start_anchor="1",
        from_end_anchor="1",
        source_roles="START:ORF,END:ORF",
    )
    common.update(changes)
    d1 = dict(common, primary_bucket="S:7227")
    d2 = dict(
        common,
        target_primary_bucket="S:7227",
        rid="259",
        candidate_primary_accession="P32234",
    )
    return d1, d2


def collect(tmp_path, indexes, evidence, refs=None, *, releases=None):
    idx = tsv(tmp_path / "index.tsv", D1, indexes)
    paths = {}
    for release, rows in (releases or {"2023_01": evidence}).items():
        paths[release] = tsv(tmp_path / f"{release}.tsv", D2, rows)
    refs = refs or {("2023_01", "T1", "259"): CandidateReference("S:7227", "P32234")}
    return collect_lineage(index_path=idx, evidence_paths=paths, candidates=refs)


def test_literal_matching_stable_locus(tmp_path):
    d1, d2 = input_rows()
    got = collect(tmp_path, [d1], [d2])["2023_01", "T1", "259"]
    assert got.lineage_present and not got.accession_key_present
    assert not got.collision_present
    assert got.matched_typed_keys == (("ORF", "CG8340"),)
    assert stable_unique_locus(got.matched_locus_keys, taxonomy_pass=True)
    assert not stable_unique_locus(got.matched_locus_keys, taxonomy_pass=False)


def test_multi_key_collision_does_not_erase_valid_key(tmp_path):
    d1, d2 = input_rows()
    c1, c2 = input_rows(
        key_type="ORF",
        key_value="FCAALL",
        key_target_multiplicity="20",
        key_collision="1",
    )
    a1, a2 = input_rows(key_type="ACCESSION", key_value="P32234")
    got = collect(tmp_path, [d1, c1, a1], [d2, c2, a2])["2023_01", "T1", "259"]
    assert got.accession_key_present and got.collision_present
    assert stable_unique_locus(got.matched_locus_keys, taxonomy_pass=True)


def test_start_and_end_on_different_keys_are_not_stable(tmp_path):
    a1, a2 = input_rows(
        key_value="LocusA", from_end_anchor="0", source_roles="START:ORF"
    )
    b1, b2 = input_rows(
        key_value="LocusB", from_start_anchor="0", source_roles="END:ORF"
    )
    got = collect(tmp_path, [a1, b1], [a2, b2])["2023_01", "T1", "259"]
    assert not stable_unique_locus(got.matched_locus_keys, taxonomy_pass=True)


def test_only_colliding_orf_is_not_stable(tmp_path):
    d1, d2 = input_rows(
        key_value="FCAALL", key_target_multiplicity="20", key_collision="1"
    )
    got = collect(tmp_path, [d1], [d2])["2023_01", "T1", "259"]
    assert got.collision_present
    assert not stable_unique_locus(got.matched_locus_keys, taxonomy_pass=True)


def test_accession_key_not_automatically_direct_anchor(tmp_path):
    d1, d2 = input_rows(key_type="ACCESSION", key_value="P32234")
    got = collect(tmp_path, [d1], [d2])["2023_01", "T1", "259"]
    assert got.accession_key_present
    assert got.matched_locus_keys == ()
    # Direct PRIMARY/SECONDARY is separately established by 04B.


def test_generic_so_token_never_identifies_locus(tmp_path):
    d1, d2 = input_rows(key_value="SO:0001217")
    got = collect(tmp_path, [d1], [d2])["2023_01", "T1", "259"]
    assert got.lineage_present
    assert not stable_unique_locus(got.matched_locus_keys, taxonomy_pass=True)


def test_missing_documentary_candidate_not_invented(tmp_path):
    d1, d2 = input_rows()
    refs = {
        ("2023_01", "T1", "259"): CandidateReference("S:7227", "P32234"),
        ("2023_01", "T2", "777"): CandidateReference("S:7227", "Q77777"),
    }
    got = collect(tmp_path, [d1], [d2], refs=refs)
    assert len(got) == 1
    assert ("2023_01", "T2", "777") not in got


def test_release_scoping(tmp_path):
    d1, d2 = input_rows()
    e1 = dict(d1, release="2026_03")
    e2 = dict(d2, release="2026_03")
    refs = {
        ("2023_01", "T1", "259"): CandidateReference("S:7227", "P32234"),
        ("2026_03", "T1", "259"): CandidateReference("S:7227", "P32234"),
    }
    got = collect(
        tmp_path, [d1, e1], [d2], refs=refs, releases={"2023_01": [d2], "2026_03": [e2]}
    )
    assert len(got) == 2


@pytest.mark.parametrize(
    "mutate, error",
    [
        ({"target_primary_bucket": "S:10090"}, "bucket mismatch"),
        ({"candidate_primary_accession": "WRONG"}, "accession mismatch"),
        ({"from_start_anchor": "0"}, "disagreement"),
        ({"key_value": "OTHER"}, "missing authoritative"),
        ({"key_collision": "1"}, "collision/multiplicity"),
        ({"from_end_anchor": "9"}, "expected 0/1"),
    ],
)
def test_invalid_d2_fails_closed(tmp_path, mutate, error):
    d1, d2 = input_rows()
    d2.update(mutate)
    with pytest.raises(ValueError, match=error):
        collect(tmp_path, [d1], [d2])


def test_duplicate_d1_key_fails_closed(tmp_path):
    d1, d2 = input_rows()
    with pytest.raises(ValueError, match="duplicate 04D1"):
        collect(tmp_path, [d1, dict(d1)], [d2])


def test_inconsistent_d1_collision_fails_closed(tmp_path):
    d1, d2 = input_rows(key_target_multiplicity="20", key_collision="0")
    with pytest.raises(ValueError, match="collision/multiplicity"):
        collect(tmp_path, [d1], [d2])


def test_source_role_mismatch_fails_closed(tmp_path):
    d1, d2 = input_rows()
    d2["source_roles"] = "START:ORF"
    with pytest.raises(ValueError, match="disagreement"):
        collect(tmp_path, [d1], [d2])


def test_no_accidental_percent_decoding(tmp_path):
    d1, d2 = input_rows(key_value="SO%3A0001217")
    got = collect(tmp_path, [d1], [d2])["2023_01", "T1", "259"]
    assert got.matched_typed_keys == (("ORF", "SO%3A0001217"),)
    # Scalar fields are literal, never decoded or merged by the join layer.


def test_duplicate_04d2_same_fact_is_idempotent(tmp_path):
    d1, d2 = input_rows()
    got = collect(tmp_path, [d1], [d2, dict(d2)])
    assert len(got[("2023_01", "T1", "259")].matched_typed_keys) == 1


def test_04d2_release_mismatch_fails(tmp_path):
    d1, d2 = input_rows()
    d2["release"] = "2026_03"
    with pytest.raises(ValueError, match="release contradicts"):
        collect(tmp_path, [d1], [d2])


def test_encoded_so_key_kept_literal_but_not_identifying(tmp_path):
    d1, d2 = input_rows(key_value="SO%3A0001217")
    got = collect(tmp_path, [d1], [d2])["2023_01", "T1", "259"]
    assert got.matched_typed_keys == (("ORF", "SO%3A0001217"),)
    assert got.lineage_present
    assert not stable_unique_locus(got.matched_locus_keys, taxonomy_pass=True)
