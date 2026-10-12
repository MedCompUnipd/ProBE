"""Synthetic M10c1 adapter tests (no canonical GAF, no production writes)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from probe.stage08_annotations import NormalizedGafSource
from probe.stage08_donors import DonorAssociation, DonorIndex
from probe.stage08_gaf_collection import LINK_COLUMNS, SOURCE_COLUMNS
from probe.stage08_go_closure import GoClosureIndex
from probe.stage08_go_supports import propagate_projected_assertions
from probe.stage08_m8_adapter import iter_m8_projected_assertions


def donor(target="T1", rid="7", accession="A", mode="YES"):
    return DonorAssociation(
        "2023_01",
        target,
        rid,
        ".",
        accession,
        mode,
        "DIRECT_PRIMARY_ACCESSION",
        "EXACT_SEQUENCE",
        "1",
        "PRIMARY",
        "PRIMARY",
        ".",
        "1",
    )


def write(path, rows):
    data = "".join("\t".join(row) + "\n" for row in rows).encode()
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def gaf_row(accession="A", go="GO:0000002", code="IDA", qualifier="enables"):
    return (
        "UniProtKB",
        accession,
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


@pytest.fixture
def bundle(tmp_path):
    from probe.stage08_annotations import SourceAssertion
    from probe.stage08_gaf_collection import _record

    origin = NormalizedGafSource("2023_01", "/never/open/source.gaf", "b" * 64)
    source_rows = []
    link_rows = []
    specs = [
        (3, gaf_row(), [("T1", "7"), ("T2", "8")]),
        (
            15,
            gaf_row(go="GO:0000001", qualifier="NOT|enables", code="IKR"),
            [("T1", "7"), ("T2", "8")],
        ),
    ]
    for line_number, fields, links in specs:
        annotation = _record(fields, path=origin.path, line_number=line_number)
        assertion = SourceAssertion(origin, line_number, annotation, fields)
        source_rows.append(
            (
                assertion.assertion_id,
                origin.release,
                str(line_number),
                *fields,
            )
        )
        link_rows.extend(
            (origin.release, target, rid, assertion.assertion_id)
            for target, rid in links
        )
    source_tsv = tmp_path / "sources.tsv"
    links_tsv = tmp_path / "links.tsv"
    args = dict(
        source_tsv=source_tsv,
        links_tsv=links_tsv,
        gaf_source=origin,
        donors=DonorIndex(
            [donor(), donor("T2", "8")],
            start_release_id="2023_01",
            end_release_id="2026_03",
            source_database="UniProtKB",
        ),
        expected_source_rows=2,
        expected_links=4,
        source_tsv_sha256=write(source_tsv, [SOURCE_COLUMNS, *source_rows]),
        links_tsv_sha256=write(links_tsv, [LINK_COLUMNS, *link_rows]),
    )
    return args


def test_full_join_and_unchanged_evidence_and_not(bundle):
    items = list(iter_m8_projected_assertions(**bundle))
    assert len(items) == 2
    assert [i.assertion.line_number for i in items] == [3, 15]
    assert {len(i.donors) for i in items} == {2}
    graph = GoClosureIndex(
        {"GO:0000001": "F", "GO:0000002": "F"},
        [("GO:0000002", "is_a", "GO:0000001")],
    )
    rows = list(propagate_projected_assertions(items, graph=graph))
    assert len(rows) == 8
    assert {r.evidence_code for r in rows if r.polarity == "NOT"} == {"IKR"}
    assert {r.gaf_qualifier for r in rows if r.polarity == "NOT"} == {"NOT|enables"}
    assert {r.rid for r in rows} == {"7", "8"}
    assert {r.normalized_gaf_line for r in rows} == {3, 15}


def test_mismatch_source_digest_rejected_at_eof(bundle):
    bundle["source_tsv_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="SHA256"):
        list(iter_m8_projected_assertions(**bundle))


def test_mismatch_link_digest_rejected_at_eof(bundle):
    bundle["links_tsv_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="SHA256"):
        list(iter_m8_projected_assertions(**bundle))


def test_source_count_mismatch(bundle):
    bundle["expected_source_rows"] = 3
    with pytest.raises(ValueError, match="source-row count"):
        list(iter_m8_projected_assertions(**bundle))


def test_link_count_mismatch(bundle):
    bundle["expected_links"] = 5
    with pytest.raises(ValueError, match="link count"):
        list(iter_m8_projected_assertions(**bundle))


def test_unknown_donor_fails_closed(bundle):
    path = bundle["links_tsv"]
    lines = path.read_text().splitlines()
    cells = lines[1].split("\t")
    cells[2] = "999"
    lines[1] = "\t".join(cells)
    bundle["links_tsv_sha256"] = write(
        path, [tuple(line.split("\t")) for line in lines]
    )
    with pytest.raises(ValueError, match="authorized donor"):
        list(iter_m8_projected_assertions(**bundle))


def test_duplicate_link_fails_closed(bundle):
    path = bundle["links_tsv"]
    lines = path.read_text().splitlines()
    lines.insert(2, lines[1])
    bundle["links_tsv_sha256"] = write(
        path, [tuple(line.split("\t")) for line in lines]
    )
    bundle["expected_links"] += 1
    with pytest.raises(ValueError, match="repeated donor link"):
        list(iter_m8_projected_assertions(**bundle))


def test_noncontiguous_link_fails_closed(bundle):
    path = bundle["links_tsv"]
    lines = path.read_text().splitlines()
    lines[1], lines[3] = lines[3], lines[1]
    bundle["links_tsv_sha256"] = write(
        path, [tuple(line.split("\t")) for line in lines]
    )
    with pytest.raises(ValueError, match="immediately matching"):
        list(iter_m8_projected_assertions(**bundle))


def test_source_id_mismatch_fails_closed(bundle):
    path = bundle["source_tsv"]
    lines = path.read_text().splitlines()
    fields = lines[1].split("\t")
    fields[0] = "f" * 64
    lines[1] = "\t".join(fields)
    bundle["source_tsv_sha256"] = write(
        path, [tuple(line.split("\t")) for line in lines]
    )
    with pytest.raises(ValueError, match="assertion ID"):
        list(iter_m8_projected_assertions(**bundle))


def test_donor_no_is_never_accepted(bundle):
    bundle["donors"] = DonorIndex(
        [donor(mode="NO"), donor("T2", "8")],
        start_release_id="2023_01",
        end_release_id="2026_03",
        source_database="UniProtKB",
    )
    with pytest.raises(ValueError, match="authorized donor"):
        list(iter_m8_projected_assertions(**bundle))


def test_source_out_of_order_fails(bundle):
    path = bundle["source_tsv"]
    lines = path.read_text().splitlines()
    lines[1], lines[2] = lines[2], lines[1]
    bundle["source_tsv_sha256"] = write(
        path, [tuple(line.split("\t")) for line in lines]
    )
    with pytest.raises(ValueError, match="immediately matching"):
        list(iter_m8_projected_assertions(**bundle))


def test_header_corruption_fails(bundle):
    path = bundle["source_tsv"]
    lines = path.read_text().splitlines()
    lines[0] = "garbage"
    bundle["source_tsv_sha256"] = write(
        path, [tuple(line.split("\t")) for line in lines]
    )
    with pytest.raises(ValueError, match="header"):
        list(iter_m8_projected_assertions(**bundle))


def test_symlink_rejected(bundle, tmp_path):
    alias = tmp_path / "alias.tsv"
    alias.symlink_to(bundle["source_tsv"])
    bundle["source_tsv"] = alias
    with pytest.raises(ValueError, match="non-symlink"):
        list(iter_m8_projected_assertions(**bundle))


def test_empty_tables_complete(tmp_path, bundle):
    bundle["source_tsv_sha256"] = write(bundle["source_tsv"], [SOURCE_COLUMNS])
    bundle["links_tsv_sha256"] = write(bundle["links_tsv"], [LINK_COLUMNS])
    bundle["expected_source_rows"] = 0
    bundle["expected_links"] = 0
    assert list(iter_m8_projected_assertions(**bundle)) == []


def test_no_open_original_gaf(bundle, monkeypatch):
    original = Path.open

    def guarded_open(path, *args, **kwargs):
        if str(path) == "/never/open/source.gaf":
            raise AssertionError("Original GAF read forbidden")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    assert len(list(iter_m8_projected_assertions(**bundle))) == 2
