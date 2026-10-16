"""Integrated M10c2/c3 tests: no GAF, only synthetic M8-derived TSVs."""

from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from probe.stage08_annotations import NormalizedGafSource, SourceAssertion
from probe.stage08_compact_interpretation import (
    RoleTables,
    build_compact_interpretation,
    iter_term_provenance,
)
from probe.stage08_donors import DonorAssociation, DonorIndex
from probe.stage08_gaf_collection import LINK_COLUMNS, SOURCE_COLUMNS, _record
from probe.stage08_go_closure import GoClosureIndex


def donor(release, target, rid, accession="A"):
    return DonorAssociation(
        release,
        target,
        rid,
        ".",
        accession,
        "YES",
        "DIRECT_PRIMARY_ACCESSION",
        "EXACT_SEQUENCE",
        "1",
        "PRIMARY",
        "PRIMARY",
        ".",
        "1",
    )


def gaf_fields(go="GO:0000003", qualifier="enables", code="IDA"):
    return (
        "UniProtKB",
        "A",
        "GENE",
        qualifier,
        go,
        "PMID:123",
        code,
        "",
        "F",
        "Protein",
        "",
        "protein",
        "taxon:1",
        "20230101",
        "GO",
        "",
        "",
    )


def write(path, rows):
    payload = "".join("\t".join(row) + "\n" for row in rows).encode()
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def make_role(tmp_path, role, release, records):
    source = NormalizedGafSource(release, f"/never/open/{role}.gaf", "b" * 64)
    source_rows = []
    link_rows = []
    for line, go, qualifier, code, links in records:
        fields = gaf_fields(go=go, qualifier=qualifier, code=code)
        assertion = SourceAssertion(
            source,
            line,
            _record(fields, path=source.path, line_number=line),
            fields,
        )
        source_rows.append(
            (
                assertion.assertion_id,
                release,
                str(line),
                *fields,
            )
        )
        link_rows.extend(
            (release, target, rid, assertion.assertion_id) for target, rid in links
        )
    src = tmp_path / f"{role}_src.tsv"
    lnk = tmp_path / f"{role}_lnk.tsv"
    return RoleTables(
        role,
        src,
        lnk,
        source,
        len(source_rows),
        len(link_rows),
        write(src, [SOURCE_COLUMNS, *source_rows]),
        write(lnk, [LINK_COLUMNS, *link_rows]),
    )


@pytest.fixture
def inputs(tmp_path):
    start = make_role(
        tmp_path,
        "START",
        "2023_01",
        [
            (3, "GO:0000003", "enables", "IDA", [("T1", "7"), ("T2", "8")]),
            (8, "GO:0000002", "NOT|enables", "IKR", [("T1", "7")]),
            (10, "GO:0000003", "enables", "IEA", [("T1", "7")]),
        ],
    )
    end = make_role(
        tmp_path,
        "END",
        "2026_03",
        [
            (5, "GO:0000003", "enables", "EXP", [("T1", "9")]),
        ],
    )
    donors = DonorIndex(
        [
            donor("2023_01", "T1", "7"),
            donor("2023_01", "T2", "8"),
            donor("2026_03", "T1", "9"),
        ],
        start_release_id="2023_01",
        end_release_id="2026_03",
        source_database="UniProtKB",
    )
    graph = GoClosureIndex(
        {f"GO:{n:07d}": "F" for n in range(1, 4)},
        [("GO:0000003", "is_a", "GO:0000002"), ("GO:0000002", "is_a", "GO:0000001")],
    )
    return (start, end), donors, graph


def build(tmp_path, inputs):
    roles, donors, graph = inputs
    destination = tmp_path / "interpretation"
    report = build_compact_interpretation(
        roles=roles,
        donors=donors,
        graph=graph,
        ontology_sha256="a" * 64,
        destination=destination,
    )
    return destination, report


def summary(destination):
    values = [
        line.split("\t")
        for line in (destination / "propagated_terms.tsv").read_text().splitlines()
    ]
    return {tuple(row[:4]): row[4:] for row in values[1:]}


def test_end_to_end_redundancy_not_and_conflict(tmp_path, inputs):
    destination, report = build(tmp_path, inputs)
    assert report["by_role"]["START"] == {
        "retrieved_source_rows": 3,
        "annotation_links": 4,
    }
    assert report["by_role"]["END"] == {
        "retrieved_source_rows": 1,
        "annotation_links": 1,
    }
    assert report["unique_direct_groups"] == 5
    assert report["expanded_group_terms"] == 14
    assert report["positive_not_conflicts"] == 2
    rows = summary(destination)
    key = ("2023_01", "T1", "F", "GO:0000003")
    assert rows[key] == ["2", "1", '["IDA","IEA"]', '["IKR"]', "2", "0", "1"]
    parent = ("2023_01", "T1", "F", "GO:0000001")
    assert rows[parent] == ["2", "0", '["IDA","IEA"]', "[]", "0", "0", "0"]
    assert ("2023_01", "T2", "F", "GO:0000003") in rows
    assert ("2026_03", "T1", "F", "GO:0000003") in rows
    assert report["evidence_policy"] == "unfiltered_literal_source_codes"
    assert report["evaluation_policy"] == "not_computed"
    manifest = json.loads((destination / "manifest.json").read_text())
    for filename, data in manifest["outputs"].items():
        assert (
            hashlib.sha256((destination / filename).read_bytes()).hexdigest()
            == data["sha256"]
        )


def test_recover_inherited_and_negative_source_donors(tmp_path, inputs):
    destination, _ = build(tmp_path, inputs)
    db = destination / "direct_provenance.sqlite3"
    graph = inputs[2]
    ancestors = list(
        iter_term_provenance(
            database=db,
            graph=graph,
            release="2023_01",
            target_id="T1",
            aspect="F",
            go_id="GO:0000001",
            polarity="POSITIVE",
        )
    )
    assert len(ancestors) == 2
    assert {r[3] for r in ancestors} == {"IDA", "IEA"}
    assert {r[2] for r in ancestors} == {"GO:0000003"}
    not_rows = list(
        iter_term_provenance(
            database=db,
            graph=graph,
            release="2023_01",
            target_id="T1",
            aspect="F",
            go_id="GO:0000003",
            polarity="NOT",
        )
    )
    assert len(not_rows) == 1
    assert not_rows[0][2] == "GO:0000002"
    assert not_rows[0][3] == "IKR"
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM direct_support").fetchone()[0] == 5
        assert (
            conn.execute("SELECT COUNT(*) FROM term_code_support").fetchone()[0] == 14
        )


def test_bad_digest_cannot_publish(tmp_path, inputs):
    roles, donors, graph = inputs
    bad = RoleTables(
        roles[1].role,
        roles[1].source_tsv,
        roles[1].links_tsv,
        roles[1].gaf_source,
        roles[1].expected_source_rows,
        roles[1].expected_links,
        "0" * 64,
        roles[1].links_tsv_sha256,
    )
    with pytest.raises(ValueError, match="SHA256"):
        build_compact_interpretation(
            roles=(roles[0], bad),
            donors=donors,
            graph=graph,
            ontology_sha256="a" * 64,
            destination=tmp_path / "bad",
        )
    assert not (tmp_path / "bad").exists()
    assert not list(tmp_path.glob(".m10c_staging_*"))


def test_ineligible_graph_term_cannot_publish(tmp_path, inputs):
    roles, donors, _ = inputs
    graph = GoClosureIndex({"GO:0000001": "F"}, [])
    with pytest.raises(ValueError, match="absent from the pinned graph"):
        build_compact_interpretation(
            roles=roles,
            donors=donors,
            graph=graph,
            ontology_sha256="a" * 64,
            destination=tmp_path / "bad",
        )
    assert not (tmp_path / "bad").exists()


def test_completed_destination_never_replaced(tmp_path, inputs):
    destination, _ = build(tmp_path, inputs)
    before = (destination / "manifest.json").read_bytes()
    with pytest.raises(FileExistsError, match="replace"):
        build(tmp_path, inputs)
    assert (destination / "manifest.json").read_bytes() == before


def test_unknown_role_and_duplicate_release_rejected(tmp_path, inputs):
    roles, donors, graph = inputs
    with pytest.raises(ValueError, match="independent START and END"):
        build_compact_interpretation(
            roles=(roles[0], roles[0]),
            donors=donors,
            graph=graph,
            ontology_sha256="a" * 64,
            destination=tmp_path / "bad",
        )
    same = RoleTables(
        "END",
        roles[1].source_tsv,
        roles[1].links_tsv,
        roles[0].gaf_source,
        1,
        1,
        roles[1].source_tsv_sha256,
        roles[1].links_tsv_sha256,
    )
    with pytest.raises(ValueError, match="distinct releases"):
        build_compact_interpretation(
            roles=(roles[0], same),
            donors=donors,
            graph=graph,
            ontology_sha256="a" * 64,
            destination=tmp_path / "bad",
        )


def test_not_never_removed_by_positive_evidence(tmp_path, inputs):
    destination, _ = build(tmp_path, inputs)
    rows = summary(destination)
    conflicting = rows[("2023_01", "T1", "F", "GO:0000002")]
    assert conflicting[0] == "2" and conflicting[1] == "1"
    assert conflicting[-1] == "1"
    assert "EVAL" not in (destination / "propagated_terms.tsv").read_text()
    assert "SKIP" not in (destination / "propagated_terms.tsv").read_text()


def test_final_integrity_gate_failure_cleans_unpublished_staging(
    tmp_path,
    inputs,
):
    roles, donors, graph = inputs

    def fail():
        raise ValueError("Source implementation changed")

    with pytest.raises(ValueError, match="implementation changed"):
        build_compact_interpretation(
            roles=roles,
            donors=donors,
            graph=graph,
            ontology_sha256="a" * 64,
            destination=tmp_path / "bad",
            final_check=fail,
        )
    assert not (tmp_path / "bad").exists()
    assert not list(tmp_path.glob(".m10c_staging_*"))


def test_source_links_and_m10b_propagation_count_parity(tmp_path, inputs):
    from collections import Counter

    from probe.stage08_go_supports import propagate_projected_assertions
    from probe.stage08_m8_adapter import iter_m8_projected_assertions

    destination, _ = build(tmp_path, inputs)
    roles, donors, graph = inputs
    expected = Counter()
    for role in roles:
        projected = iter_m8_projected_assertions(
            source_tsv=role.source_tsv,
            links_tsv=role.links_tsv,
            gaf_source=role.gaf_source,
            donors=donors,
            expected_source_rows=role.expected_source_rows,
            expected_links=role.expected_links,
            source_tsv_sha256=role.source_tsv_sha256,
            links_tsv_sha256=role.links_tsv_sha256,
        )
        for row in propagate_projected_assertions(projected, graph=graph):
            expected[
                (
                    row.release,
                    row.target_id,
                    row.aspect,
                    row.go_id,
                    row.polarity,
                    row.evidence_code,
                )
            ] += 1
    with sqlite3.connect(destination / "direct_provenance.sqlite3") as conn:
        actual = {
            tuple(row[:6]): row[6]
            for row in conn.execute("""
                SELECT release, target_id, aspect, go_id, polarity,
                    evidence_code, support_count FROM term_code_support
            """)
        }
    assert actual == expected


def test_missing_second_role_and_invalid_metadata_rejected(tmp_path, inputs):
    roles, donors, graph = inputs
    with pytest.raises(ValueError, match="independent START and END"):
        build_compact_interpretation(
            roles=roles[:1],
            donors=donors,
            graph=graph,
            ontology_sha256="a" * 64,
            destination=tmp_path / "bad",
        )
    with pytest.raises(ValueError, match="ontology fingerprint"):
        build_compact_interpretation(
            roles=roles,
            donors=donors,
            graph=graph,
            ontology_sha256="invalid",
            destination=tmp_path / "bad",
        )


def test_absence_of_negatives_is_not_implied_by_missing_support(tmp_path, inputs):
    destination, _ = build(tmp_path, inputs)
    rows = summary(destination)
    for key, row in rows.items():
        if key[1] == "T2":
            assert row[1] == "0"
            assert row[3] == "[]"
            assert row[-1] == "0"


def test_provenance_negative_and_positive_aspects_isolated(tmp_path, inputs):
    destination, _ = build(tmp_path, inputs)
    graph = inputs[2]
    with pytest.raises(ValueError, match="aspect mismatch"):
        list(
            iter_term_provenance(
                database=destination / "direct_provenance.sqlite3",
                graph=graph,
                release="2023_01",
                target_id="T1",
                aspect="P",
                go_id="GO:0000002",
                polarity="POSITIVE",
            )
        )
    with pytest.raises(ValueError, match="Invalid polarity"):
        list(
            iter_term_provenance(
                database=destination / "direct_provenance.sqlite3",
                graph=graph,
                release="2023_01",
                target_id="T1",
                aspect="F",
                go_id="GO:0000002",
                polarity="UNSPECIFIED",
            )
        )


def test_repeated_sources_collapse_without_losing_donor_count(tmp_path):
    rows = [(n, "GO:0000003", "enables", "IDA", [("T1", "7")]) for n in range(1, 1002)]
    start = make_role(tmp_path, "START", "2023_01", rows)
    end = make_role(tmp_path, "END", "2026_03", [])
    donors = DonorIndex(
        [donor("2023_01", "T1", "7")],
        start_release_id="2023_01",
        end_release_id="2026_03",
        source_database="UniProtKB",
    )
    graph = GoClosureIndex(
        {f"GO:{n:07d}": "F" for n in range(1, 4)},
        [("GO:0000003", "is_a", "GO:0000002"), ("GO:0000002", "is_a", "GO:0000001")],
    )
    report = build_compact_interpretation(
        roles=(start, end),
        donors=donors,
        graph=graph,
        ontology_sha256="a" * 64,
        destination=tmp_path / "full",
    )
    assert report["expanded_group_terms"] == 3
    assert report["unique_direct_groups"] == 1
    assert report["distinct_propagated_terms"] == 3
    first = summary(tmp_path / "full")
    assert first[("2023_01", "T1", "F", "GO:0000001")][:2] == ["1001", "0"]
    with sqlite3.connect(tmp_path / "full/direct_provenance.sqlite3") as conn:
        assert conn.execute("SELECT COUNT(*) FROM direct_support").fetchone()[0] == 1001
        assert conn.execute("SELECT COUNT(*) FROM term_code_support").fetchone()[0] == 3


def test_staging_sqlite_avoids_disk_journal_and_remains_readable(tmp_path):
    from probe.stage08_compact_interpretation import _database

    db = tmp_path / "staging.sqlite3"
    conn = _database(db)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "memory"
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 0
        assert conn.execute("PRAGMA temp_store").fetchone()[0] == 1
        assert conn.execute("PRAGMA cache_size").fetchone()[0] == -131072
        conn.execute(
            "INSERT INTO direct_support VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "2023_01",
                "T1",
                "F",
                "GO:0000001",
                "POSITIVE",
                "IDA",
                "7",
                "assertion",
                1,
                "A",
                "enables",
                "",
            ),
        )
        conn.commit()
        assert not (tmp_path / "staging.sqlite3-journal").exists()
    finally:
        conn.close()
    with sqlite3.connect(db) as reader:
        assert reader.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert reader.execute("SELECT COUNT(*) FROM direct_support").fetchone()[0] == 1
