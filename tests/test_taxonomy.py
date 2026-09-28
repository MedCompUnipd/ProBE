from __future__ import annotations

import socket
import sqlite3

import pytest

from probe.taxonomy import NcbiTaxonomyIndex


def _write_taxdump(tmp_path):
    nodes = tmp_path / "nodes.dmp"
    names = tmp_path / "names.dmp"
    merged = tmp_path / "merged.dmp"
    delnodes = tmp_path / "delnodes.dmp"
    nodes.write_text(
        "1\t|\t1\t|\tno rank\t|\n"
        "2\t|\t1\t|\tsuperkingdom\t|\n"
        "9606\t|\t9605\t|\tspecies\t|\n",
        encoding="utf-8",
    )
    names.write_text(
        "1\t|\troot\t|\t\t|\tscientific name\t|\n"
        "2\t|\tBacteria\t|\t\t|\tscientific name\t|\n"
        "9606\t|\tHomo sapiens\t|\t\t|\tscientific name\t|\n"
        "9606\t|\thuman\t|\t\t|\tcommon name\t|\n",
        encoding="utf-8",
    )
    merged.write_text("999\t|\t9606\t|\n", encoding="utf-8")
    delnodes.write_text("666\t|\n", encoding="utf-8")
    return nodes, names, merged, delnodes


def _build(tmp_path, **kwargs):
    nodes, names, merged, delnodes = _write_taxdump(tmp_path)
    return NcbiTaxonomyIndex.build(
        tmp_path / "taxonomy.sqlite3",
        nodes=nodes,
        names=names,
        merged=merged,
        delnodes=delnodes,
        snapshot_id="synthetic-snapshot",
        **kwargs,
    )


def test_taxonomy_index_stores_nodes_names_merges_deletions_and_provenance(tmp_path):
    with _build(tmp_path) as index:
        assert index.schema_version == 1
        assert index.node("9606") is not None
        assert index.node("9606").parent_taxid == "9605"  # type: ignore[union-attr]
        assert index.node("9606").rank == "species"  # type: ignore[union-attr]
        assert index.node("9606").scientific_name == "Homo sapiens"  # type: ignore[union-attr]
        root = index.node("1")
        assert root is not None
        assert root.parent_taxid == "1"
        assert index.node("404") is None
        assert index.merged_taxid("999") == "9606"
        assert index.merged_taxid("9606") is None
        assert index.is_deleted("666")
        assert not index.is_deleted("9606")
        assert index.provenance.snapshot_id == "synthetic-snapshot"
        assert [source.member for source in index.provenance.sources] == [
            "delnodes.dmp",
            "merged.dmp",
            "names.dmp",
            "nodes.dmp",
        ]


def test_taxonomy_index_rebuild_replaces_rows_deterministically(tmp_path):
    with _build(tmp_path) as index:
        first_provenance = index.provenance
        assert index.node("9606") is not None

    with _build(tmp_path) as rebuilt:
        assert rebuilt.provenance == first_provenance
        assert rebuilt.merged_taxid("999") == "9606"

    with sqlite3.connect(tmp_path / "taxonomy.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM taxonomy_nodes").fetchone() == (
            3,
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM taxonomy_merged"
        ).fetchone() == (1,)


def test_taxonomy_index_uses_only_local_taxdump_files(tmp_path, monkeypatch):
    def network_must_not_be_called(*_args, **_kwargs):
        raise AssertionError("taxonomy index must not use the network")

    monkeypatch.setattr(socket, "create_connection", network_must_not_be_called)
    with _build(tmp_path) as index:
        assert index.node("2") is not None


def test_taxonomy_index_rejects_invalid_rows_without_creating_valid_index(tmp_path):
    nodes, names, merged, delnodes = _write_taxdump(tmp_path)
    nodes.write_text("invalid\t|\n", encoding="utf-8")
    index_path = tmp_path / "taxonomy.sqlite3"

    with pytest.raises(ValueError, match="invalid nodes.dmp row"):
        NcbiTaxonomyIndex.build(
            index_path,
            nodes=nodes,
            names=names,
            merged=merged,
            delnodes=delnodes,
        )

    with pytest.raises(ValueError, match="not a ProBE NCBI taxonomy index"):
        NcbiTaxonomyIndex(index_path)


def test_taxonomy_index_rejects_duplicate_scientific_names(tmp_path):
    nodes, names, merged, delnodes = _write_taxdump(tmp_path)
    with names.open("a", encoding="utf-8") as handle:
        handle.write("9606\t|\tOther human\t|\t\t|\tscientific name\t|\n")

    with pytest.raises(ValueError, match="duplicate or unknown scientific name"):
        NcbiTaxonomyIndex.build(
            tmp_path / "taxonomy.sqlite3",
            nodes=nodes,
            names=names,
            merged=merged,
            delnodes=delnodes,
        )


def test_taxonomy_index_failed_rebuild_preserves_previous_snapshot(tmp_path):
    with _build(tmp_path):
        pass
    nodes, names, merged, delnodes = _write_taxdump(tmp_path)
    nodes.write_text("invalid\t|\n", encoding="utf-8")
    index_path = tmp_path / "taxonomy.sqlite3"

    with pytest.raises(ValueError, match="invalid nodes.dmp row"):
        NcbiTaxonomyIndex.build(
            index_path,
            nodes=nodes,
            names=names,
            merged=merged,
            delnodes=delnodes,
        )

    with NcbiTaxonomyIndex(index_path) as index:
        assert index.node("9606") is not None
