"""Synthetic fail-closed checks for the canonical candidate provenance ledger."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from probe.canonical_v2.candidate_assembly import assemble_candidate_provenance
from probe.canonical_v2.contracts import (
    DISCOVERY_REQUIRED,
    FULL_SOURCE_REQUIRED,
    GO_SOURCE_REQUIRED,
    LINEAGE_REQUIRED,
    OUTPUT_NAME,
    RESCUE_REQUIRED,
    SUMMARY_NAME,
)


def _write(path: Path, fields: frozenset[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=sorted(fields),
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fields})


def _scanner(rid: str, accession: str, taxid: str) -> dict:
    record = {field: "1" for field in GO_SOURCE_REQUIRED}
    record.update(
        target_id="T",
        rid=rid,
        accession=accession,
        raw_ox=taxid,
        resolved_taxid=taxid,
        species_taxid="100",
        frag="0",
        record_number="1",
        target_bucket="S:100",
        exact_sequence="0",
        candidate_length="15",
        target_length="15",
    )
    return record


def _discovery(rid: str, accession: str, taxid: str, *, full: bool) -> dict:
    record = {field: "" for field in DISCOVERY_REQUIRED}
    record.update(
        release="R1",
        target_id="T",
        rid=rid,
        candidate_accession=accession,
        candidate_raw_ox=taxid,
        target_resolved_taxid="111",
        candidate_resolved_taxid=taxid,
        species_taxid="100",
        go_positive_species_wide=str(int(not full)),
        full_exact_species_wide=str(int(full)),
        go_positive_exact="0",
        go_positive_record_number="" if full else "1",
        full_record_number="9" if full else "",
        strict_gate="DENIED" if full else "DEFER_TO_DOCUMENTARY",
        same_species_exact_gate=(
            "ELIGIBLE" if full else "DEFER_TO_DOCUMENTARY"
        ),
    )
    return record


def _lineage(rid: str, accession: str, *, frag: str = "0") -> dict:
    row = {field: "1" for field in LINEAGE_REQUIRED}
    row.update(
        release="R1",
        target_id="T",
        rid=rid,
        record_number=rid,
        candidate_primary_accession=accession,
        raw_ox="111",
        resolved_taxid="111",
        species_taxid="100",
        frag=frag,
        lineage_fact_signature="example-signature",
        target_accession_keys="DOCUMENTARY_KEY",
    )
    return row


def _fixtures(tmp_path: Path) -> dict:
    go = _scanner("101", "A101", "111")
    full = {field: "1" for field in FULL_SOURCE_REQUIRED}
    full.update(
        release="R1",
        target_id="T",
        rid="102",
        record_number="9",
        accession="A102",
        raw_ox="112",
        target_resolved_taxid="111",
        candidate_resolved_taxid="112",
        species_taxid="100",
        frag="0",
        exact_full_sequence="1",
        discovery_channel="FULL_V2_EXACT",
    )
    rescue = _scanner("104", "A104", "111")
    rescue["record_number"] = "104"
    paths = {
        "discovery_union": tmp_path / "discovery.tsv",
        "go_positive_pairs": tmp_path / "go.tsv",
        "full_exact_pairs": tmp_path / "full.tsv",
        "lineage_pairs": tmp_path / "lineage.tsv",
        "rescue_pairs": tmp_path / "rescue.tsv",
    }
    sources = {
        "discovery_union": (
            DISCOVERY_REQUIRED,
            [
                _discovery("101", "A101", "111", full=False),
                _discovery("102", "A102", "112", full=True),
            ],
        ),
        "go_positive_pairs": (GO_SOURCE_REQUIRED, [go]),
        "full_exact_pairs": (FULL_SOURCE_REQUIRED, [full]),
        "lineage_pairs": (
            LINEAGE_REQUIRED,
            [_lineage("101", "A101"), _lineage("103", "A103", frag="1")],
        ),
        "rescue_pairs": (RESCUE_REQUIRED, [rescue]),
    }
    for name, (header, rows) in sources.items():
        _write(paths[name], header, rows)
    return paths


def _assemble(tmp_path: Path, paths: dict) -> dict:
    return assemble_candidate_provenance(
        release="R1", output_dir=tmp_path / "published", **paths
    )


def _rows(tmp_path: Path) -> dict[str, dict]:
    with (tmp_path / "published" / OUTPUT_NAME).open() as stream:
        return {
            row["rid"]: row for row in csv.DictReader(stream, delimiter="\t")
        }


def test_four_channels_and_lineage_fragment_preserved(tmp_path: Path) -> None:
    summary = _assemble(tmp_path, _fixtures(tmp_path))
    rows = _rows(tmp_path)
    assert summary["counts"]["unique_pairs"] == 4
    assert set(rows) == {"101", "102", "103", "104"}
    assert rows["101"]["go_positive_species_wide"] == "1"
    assert rows["101"]["lineage_discovered"] == "1"
    assert rows["102"]["same_species_exact_gate"] == "ELIGIBLE"
    assert rows["103"]["frag"] == "1"
    assert rows["103"]["lineage_discovered"] == "1"
    assert rows["103"]["go_positive_species_wide"] == "0"
    assert rows["104"]["full_v2_rescue_discovered"] == "1"
    raw = json.loads(rows["103"]["lineage_source_json"])
    assert raw["target_accession_keys"] == "DOCUMENTARY_KEY"
    assert rows["103"]["strict_gate"] == ""
    assert summary["go_donor_decisions"] == 0
    assert summary["taxonomy_audit_promotions"] == 0


def test_summary_checksums_and_no_sqlite_publication(tmp_path: Path) -> None:
    paths = _fixtures(tmp_path)
    summary = _assemble(tmp_path, paths)
    published = tmp_path / "published"
    assert set(path.name for path in published.iterdir()) == {
        OUTPUT_NAME,
        SUMMARY_NAME,
    }
    content = (published / OUTPUT_NAME).read_bytes()
    assert summary["output_sha256"][OUTPUT_NAME] == hashlib.sha256(
        content
    ).hexdigest()
    for name, path in paths.items():
        assert summary["input_sha256"][name] == hashlib.sha256(
            path.read_bytes()
        ).hexdigest()


def test_duplicate_go_source_fails_closed(tmp_path: Path) -> None:
    paths = _fixtures(tmp_path)
    path = paths["go_positive_pairs"]
    with path.open() as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    _write(path, GO_SOURCE_REQUIRED, [*rows, *rows])
    with pytest.raises(ValueError, match="Duplicate go"):
        _assemble(tmp_path, paths)
    assert not (tmp_path / "published").exists()


def test_conflicting_identity_fails_closed(tmp_path: Path) -> None:
    paths = _fixtures(tmp_path)
    path = paths["lineage_pairs"]
    with path.open() as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    rows[0]["resolved_taxid"] = "222"
    _write(path, LINEAGE_REQUIRED, rows)
    with pytest.raises(ValueError, match="Conflicting identity"):
        _assemble(tmp_path, paths)
    assert not (tmp_path / "published").exists()


def test_scanner_pair_missing_from_union_rejected(tmp_path: Path) -> None:
    paths = _fixtures(tmp_path)
    path = paths["discovery_union"]
    with path.open() as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    _write(path, DISCOVERY_REQUIRED, rows[1:])
    with pytest.raises(ValueError, match="Scanner pair missing"):
        _assemble(tmp_path, paths)


def test_discovery_missing_raw_pair_rejected(tmp_path: Path) -> None:
    paths = _fixtures(tmp_path)
    _write(paths["full_exact_pairs"], FULL_SOURCE_REQUIRED, [])
    with pytest.raises(ValueError, match="Discovery keys without"):
        _assemble(tmp_path, paths)


def test_rescue_overlap_preserves_two_sources(tmp_path: Path) -> None:
    paths = _fixtures(tmp_path)
    rescue = _scanner("101", "A101", "111")
    rescue["record_number"] = "101"
    _write(paths["rescue_pairs"], RESCUE_REQUIRED, [rescue])
    summary = _assemble(tmp_path, paths)
    assert summary["counts"]["rescue_overlaps"] == 1
    assert _rows(tmp_path)["101"]["rescue_source_json"]
    assert _rows(tmp_path)["101"]["lineage_source_json"]


def test_rescue_wrong_ordinal_refused(tmp_path: Path) -> None:
    paths = _fixtures(tmp_path)
    path = paths["rescue_pairs"]
    with path.open() as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    rows[0]["record_number"] = "11"
    _write(path, RESCUE_REQUIRED, rows)
    with pytest.raises(ValueError, match="Rescue FULL record"):
        _assemble(tmp_path, paths)


def test_wrong_release_refused(tmp_path: Path) -> None:
    paths = _fixtures(tmp_path)
    path = paths["lineage_pairs"]
    with path.open() as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    rows[0]["release"] = "OTHER"
    _write(path, LINEAGE_REQUIRED, rows)
    with pytest.raises(ValueError, match="Unexpected source release"):
        _assemble(tmp_path, paths)


def test_inconsistent_gate_refused(tmp_path: Path) -> None:
    paths = _fixtures(tmp_path)
    path = paths["discovery_union"]
    with path.open() as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    rows[1]["strict_gate"] = "ELIGIBLE"
    _write(path, DISCOVERY_REQUIRED, rows)
    with pytest.raises(ValueError, match="gates contradict"):
        _assemble(tmp_path, paths)


def test_destination_must_not_exist(tmp_path: Path) -> None:
    paths = _fixtures(tmp_path)
    (tmp_path / "published").mkdir()
    with pytest.raises(FileExistsError, match="occupied"):
        _assemble(tmp_path, paths)


def test_header_only_channels_allow_lineage_only(tmp_path: Path) -> None:
    paths = _fixtures(tmp_path)
    _write(paths["discovery_union"], DISCOVERY_REQUIRED, [])
    _write(paths["go_positive_pairs"], GO_SOURCE_REQUIRED, [])
    _write(paths["full_exact_pairs"], FULL_SOURCE_REQUIRED, [])
    _write(paths["rescue_pairs"], RESCUE_REQUIRED, [])
    _write(paths["lineage_pairs"], LINEAGE_REQUIRED, [_lineage("103", "A103")])
    summary = _assemble(tmp_path, paths)
    assert summary["counts"]["lineage_only"] == 1
    assert set(_rows(tmp_path)) == {"103"}


def test_unresolved_documentary_taxonomy_is_preserved(tmp_path: Path) -> None:
    paths = _fixtures(tmp_path)
    _write(paths["discovery_union"], DISCOVERY_REQUIRED, [])
    _write(paths["go_positive_pairs"], GO_SOURCE_REQUIRED, [])
    _write(paths["full_exact_pairs"], FULL_SOURCE_REQUIRED, [])
    _write(paths["rescue_pairs"], RESCUE_REQUIRED, [])
    lineage = _lineage("103", "A103")
    lineage.update(raw_ox="", resolved_taxid="", species_taxid="")
    _write(paths["lineage_pairs"], LINEAGE_REQUIRED, [lineage])
    _assemble(tmp_path, paths)
    row = _rows(tmp_path)["103"]
    assert row["candidate_resolved_taxid"] == ""
    assert row["lineage_discovered"] == "1"
    assert row["same_species_exact_gate"] == ""


def test_full_and_go_same_rid_keep_both_raw_sources(tmp_path: Path) -> None:
    paths = _fixtures(tmp_path)
    go = _scanner("102", "A102", "112")
    go.update(exact_sequence="1", record_number="8")
    _write(
        paths["go_positive_pairs"],
        GO_SOURCE_REQUIRED,
        [_scanner("101", "A101", "111"), go],
    )
    path = paths["discovery_union"]
    with path.open() as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    rows[1].update(
        go_positive_species_wide="1",
        go_positive_exact="1",
        go_positive_record_number="8",
    )
    _write(path, DISCOVERY_REQUIRED, rows)
    summary = _assemble(tmp_path, paths)
    row = _rows(tmp_path)["102"]
    assert json.loads(row["go_source_json"])["record_number"] == "8"
    assert json.loads(row["full_source_json"])["record_number"] == "9"
    assert summary["counts"]["multiple_sources"] == 2


def test_cross_source_accession_collision_refused(tmp_path: Path) -> None:
    paths = _fixtures(tmp_path)
    path = paths["lineage_pairs"]
    with path.open() as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    rows[0]["candidate_primary_accession"] = "DIFFERENT"
    _write(path, LINEAGE_REQUIRED, rows)
    with pytest.raises(ValueError, match="Conflicting identity"):
        _assemble(tmp_path, paths)


def test_bad_go_fragment_refused(tmp_path: Path) -> None:
    paths = _fixtures(tmp_path)
    path = paths["go_positive_pairs"]
    with path.open() as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    rows[0]["frag"] = "1"
    _write(path, GO_SOURCE_REQUIRED, rows)
    with pytest.raises(ValueError, match="FRAG=1"):
        _assemble(tmp_path, paths)
