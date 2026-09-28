from __future__ import annotations

import socket
import sqlite3

import pytest

from probe.taxonomy import (
    NcbiTaxonomyIndex,
    SpeciesAnchorStatus,
    TaxonomicRelationship,
    TaxonomyLineageStatus,
    TaxonomyResolutionStatus,
)


def _write_taxdump(tmp_path):
    nodes = tmp_path / "nodes.dmp"
    names = tmp_path / "names.dmp"
    merged = tmp_path / "merged.dmp"
    delnodes = tmp_path / "delnodes.dmp"
    nodes.write_text(
        "1\t|\t1\t|\tno rank\t|\n"
        "2\t|\t1\t|\tsuperkingdom\t|\n"
        "9605\t|\t1\t|\tgenus\t|\n"
        "9606\t|\t9605\t|\tspecies\t|\n"
        "123\t|\t9606\t|\tstrain\t|\n"
        "124\t|\t123\t|\tsubstrain\t|\n"
        "10088\t|\t1\t|\tgenus\t|\n"
        "10090\t|\t10088\t|\tspecies\t|\n"
        "555\t|\t404\t|\tno rank\t|\n"
        "800\t|\t801\t|\tno rank\t|\n"
        "801\t|\t800\t|\tno rank\t|\n",
        encoding="utf-8",
    )
    names.write_text(
        "1\t|\troot\t|\t\t|\tscientific name\t|\n"
        "2\t|\tBacteria\t|\t\t|\tscientific name\t|\n"
        "9606\t|\tHomo sapiens\t|\t\t|\tscientific name\t|\n"
        "10090\t|\tMus musculus\t|\t\t|\tscientific name\t|\n"
        "9606\t|\thuman\t|\t\t|\tcommon name\t|\n",
        encoding="utf-8",
    )
    merged.write_text("998\t|\t999\t|\n999\t|\t9606\t|\n", encoding="utf-8")
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
        human = index.node("9606")
        assert human is not None
        assert human.parent_taxid == "9605"
        assert human.rank == "species"
        assert human.scientific_name == "Homo sapiens"
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
            11,
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM taxonomy_merged"
        ).fetchone() == (2,)


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


def test_taxonomy_resolution_and_lineage_are_deterministic(tmp_path):
    with _build(tmp_path) as index:
        current = index.resolve_taxid("9606")
        assert current.status is TaxonomyResolutionStatus.CURRENT
        assert current.resolved_taxid == "9606"
        assert current.merge_path == ()

        one_step = index.resolve_taxid("999")
        assert one_step.status is TaxonomyResolutionStatus.MERGED
        assert one_step.resolved_taxid == "9606"
        assert one_step.merge_path == ("999", "9606")

        multi_step = index.resolve_taxid("998")
        assert multi_step.status is TaxonomyResolutionStatus.MERGED
        assert multi_step.resolved_taxid == "9606"
        assert multi_step.merge_path == ("998", "999", "9606")

        deleted = index.resolve_taxid("666")
        assert deleted.status is TaxonomyResolutionStatus.DELETED
        assert deleted.resolved_taxid is None
        assert index.resolve_taxid("404").status is TaxonomyResolutionStatus.UNRESOLVED

        lineage = index.lineage("123")
        assert lineage.status is TaxonomyLineageStatus.RESOLVED
        assert [node.taxid for node in lineage.nodes] == ["123", "9606", "9605", "1"]
        assert [node.scientific_name for node in lineage.nodes][:2] == [
            None,
            "Homo sapiens",
        ]


def test_taxonomy_resolution_detects_merge_and_parent_cycles(tmp_path):
    nodes, names, merged, delnodes = _write_taxdump(tmp_path)
    merged.write_text("700\t|\t701\t|\n701\t|\t700\t|\n", encoding="utf-8")
    with NcbiTaxonomyIndex.build(
        tmp_path / "taxonomy.sqlite3",
        nodes=nodes,
        names=names,
        merged=merged,
        delnodes=delnodes,
    ) as index:
        cycle = index.resolve_taxid("700")
        assert cycle.status is TaxonomyResolutionStatus.UNRESOLVED
        assert cycle.merge_path == ("700", "701", "700")
        broken = index.lineage("555")
        assert broken.status is TaxonomyLineageStatus.BROKEN
        assert [node.taxid for node in broken.nodes] == ["555"]
        cyclic = index.lineage("800")
        assert cyclic.status is TaxonomyLineageStatus.CYCLE
        assert [node.taxid for node in cyclic.nodes] == ["800", "801"]


def test_species_anchor_and_taxonomic_relationships(tmp_path):
    with _build(tmp_path) as index:
        strain_anchor = index.species_anchor("123")
        assert strain_anchor.status is SpeciesAnchorStatus.RESOLVED
        assert strain_anchor.species_anchor is not None
        assert strain_anchor.species_anchor.taxid == "9606"
        species_anchor = index.species_anchor("9606")
        assert species_anchor.status is SpeciesAnchorStatus.RESOLVED
        assert species_anchor.species_anchor is not None
        assert species_anchor.species_anchor.taxid == "9606"
        assert (
            index.species_anchor("1").status is SpeciesAnchorStatus.NO_SPECIES_ANCESTOR
        )

        assert (
            index.classify_taxonomic_relationship("123", "123").relationship
            is TaxonomicRelationship.SAME_TAXON
        )
        assert (
            index.classify_taxonomic_relationship("123", "124").relationship
            is TaxonomicRelationship.SAME_SPECIES_DIFFERENT_SUBTAXON
        )
        assert (
            index.classify_taxonomic_relationship("123", "10090").relationship
            is TaxonomicRelationship.CROSS_SPECIES
        )
        assert (
            index.classify_taxonomic_relationship("123", "404").relationship
            is TaxonomicRelationship.UNRESOLVED
        )
