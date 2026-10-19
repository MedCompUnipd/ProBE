"""Synthetic cold-start and fail-closed tests for rescue reference coverage."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from probe.canonical_v2.contracts import LINEAGE_REQUIRED
from probe.canonical_v2.rescue_reference import (
    CONTRACT_SCHEMA,
    COVERAGE_NAME,
    IDS_NAME,
    SUMMARY_NAME,
    plan_reference_rescue,
)
from probe.stage2c_candidate_union import GO_REQUIRED


def _write(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=fields, delimiter="\t",
            lineterminator="\n", extrasaction="ignore",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path: Path) -> dict:
    targets = [
        {"target_id": "T1", "sequence_length": "10"},
        {"target_id": "T2", "sequence_length": "10"},
        {"target_id": "T3", "sequence_length": "5"},
        {"target_id": "T4", "sequence_length": "10"},
    ]
    target_tax = [
        {"target_id": "T1", "resolved_taxid": "101",
         "species_taxid": "100", "primary_bucket": "T:101"},
        {"target_id": "T2", "resolved_taxid": "101",
         "species_taxid": "100", "primary_bucket": "S:100"},
        {"target_id": "T3", "resolved_taxid": "101",
         "species_taxid": "100", "primary_bucket": "T:101"},
        {"target_id": "T4", "resolved_taxid": "101",
         "species_taxid": "100", "primary_bucket": "T:101"},
    ]
    ox = [
        {"raw_ox": "201", "resolved_taxid": "201",
         "species_taxid": "100", "species_bucket": "S:100",
         "resolved_bucket": "T:201"},
        {"raw_ox": "101", "resolved_taxid": "101",
         "species_taxid": "100", "species_bucket": "S:100",
         "resolved_bucket": "T:101"},
    ]
    go_fields = sorted(GO_REQUIRED | {"target_bucket"})
    lin_fields = sorted(LINEAGE_REQUIRED)
    go = [
        {"target_id": "T1", "rid": "1", "accession": "P1",
         "raw_ox": "201", "resolved_taxid": "201",
         "species_taxid": "100", "target_bucket": "S:100",
         "frag": "0", "record_number": "1"},
        {"target_id": "T2", "rid": "1", "accession": "P1",
         "raw_ox": "201", "resolved_taxid": "201",
         "species_taxid": "100", "target_bucket": "S:100",
         "frag": "0", "record_number": "1"},
    ]
    lineage = [
        {"target_id": "T4", "rid": "2", "record_number": "2",
         "release": "R1", "candidate_primary_accession": "P2",
         "frag": "1"},
    ]
    anchors = [
        {"release": "R1", "target_id": "T4",
         "rid": "2", "key_source_eligible": "1"},
    ]
    paths = {
        name: tmp_path / f"{name}.tsv"
        for name in ("targets", "target_taxonomy", "ox_taxonomy",
                     "go_pairs", "lineage", "direct_anchors")
    }
    _write(paths["targets"], list(targets[0]), targets)
    _write(paths["target_taxonomy"], list(target_tax[0]), target_tax)
    _write(paths["ox_taxonomy"], list(ox[0]), ox)
    _write(paths["go_pairs"], go_fields, go)
    _write(paths["lineage"], lin_fields, lineage)
    _write(paths["direct_anchors"], list(anchors[0]), anchors)
    settings = {
        "k": 7,
        "max_required_shared": 10,
        "min_target_kmer_fraction": 0.25,
        "fragment_policy": "exclude",
        "go_positive_fasta_sha256": "a" * 64,
        "target_input_sha256": _sha(paths["targets"]),
        "target_taxonomy_sha256": _sha(paths["target_taxonomy"]),
        "ox_taxonomy_sha256": _sha(paths["ox_taxonomy"]),
    }
    contract = {
        "schema": CONTRACT_SCHEMA,
        "species_wide": {"taxonomy_policy": "species_wide", **settings},
        "reference": {"taxonomy_policy": "rank_aware", **settings},
    }
    contract_path = tmp_path / "scan_contract.json"
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    return {**{f"{name}_path": path for name, path in paths.items()},
            "scan_contract": contract_path, "contract_data": contract,
            "output_dir": tmp_path / "published", "release": "R1"}


def _call(data: dict) -> dict:
    return plan_reference_rescue(
        release=data["release"],
        scan_contract=data["scan_contract"],
        targets_path=data["targets_path"],
        target_taxonomy_path=data["target_taxonomy_path"],
        ox_taxonomy_path=data["ox_taxonomy_path"],
        go_pairs_path=data["go_pairs_path"],
        lineage_path=data["lineage_path"],
        direct_anchors_path=data["direct_anchors_path"],
        output_dir=data["output_dir"],
    )


def _change_contract(data: dict, channel: str, key: str, value: object) -> None:
    data["contract_data"][channel][key] = value
    data["scan_contract"].write_text(
        json.dumps(data["contract_data"]), encoding="utf-8"
    )


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def test_rank_aware_covers_only_eligible_taxonomic_pairs(
    tmp_path: Path,
) -> None:
    data = _fixture(tmp_path)
    summary = _call(data)
    rows = {
        row["target_id"]: row
        for row in _rows(data["output_dir"] / COVERAGE_NAME)
    }
    assert rows["T1"]["species_wide_pairs"] == "1"
    assert rows["T1"]["reference_go_pairs"] == "0"
    assert rows["T1"]["selection_reason"] == "UNCOVERED_K_SEARCHABLE"
    assert rows["T2"]["reference_go_pairs"] == "1"
    assert rows["T2"]["selection_reason"] == "REFERENCE_COVERED"
    assert rows["T3"]["selection_reason"] == "UNCOVERED_LEN_LT_K"
    assert rows["T4"]["lineage_pairs"] == "1"
    assert rows["T4"]["selection_reason"] == "REFERENCE_COVERED"
    assert (data["output_dir"] / IDS_NAME).read_text() == "T1\n"
    assert summary["counts"]["selected"] == 1
    assert summary["coverage_equivalence"].startswith("CONDITIONAL_")
    assert (data["output_dir"] / SUMMARY_NAME).is_file()


def test_strict_taxon_does_not_use_species_bucket(tmp_path: Path) -> None:
    data = _fixture(tmp_path)
    _change_contract(data, "reference", "taxonomy_policy", "strict_taxon")
    _call(data)
    assert (data["output_dir"] / IDS_NAME).read_text() == "T1\nT2\n"


def test_equal_taxid_sequence_reference_suppresses_rescue(
    tmp_path: Path,
) -> None:
    data = _fixture(tmp_path)
    rows = _rows(data["go_pairs_path"])
    rows[0]["raw_ox"] = "101"
    rows[0]["resolved_taxid"] = "101"
    _write(data["go_pairs_path"], list(rows[0]), rows)
    _call(data)
    assert (data["output_dir"] / IDS_NAME).read_text() == ""


def test_direct_lineage_anchor_must_exist(tmp_path: Path) -> None:
    data = _fixture(tmp_path)
    _write(
        data["lineage_path"],
        sorted(LINEAGE_REQUIRED), [],
    )
    with pytest.raises(ValueError, match="anchor missing"):
        _call(data)
    assert not data["output_dir"].exists()


@pytest.mark.parametrize(
    ("channel", "key", "value"),
    [
        ("reference", "k", 8),
        ("species_wide", "taxonomy_policy", "rank_aware"),
        ("reference", "fragment_policy", "include"),
        ("species_wide", "min_target_kmer_fraction", 0.5),
        ("reference", "go_positive_fasta_sha256", "b" * 64),
    ],
)
def test_mismatched_scanner_settings_fail(
    tmp_path: Path, channel: str, key: str, value: object
) -> None:
    data = _fixture(tmp_path)
    _change_contract(data, channel, key, value)
    with pytest.raises(ValueError):
        _call(data)
    assert not data["output_dir"].exists()


def test_changed_source_hash_fails(tmp_path: Path) -> None:
    data = _fixture(tmp_path)
    data["targets_path"].write_text(
        data["targets_path"].read_text() + "\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="Upstream input changed"):
        _call(data)


def test_duplicate_go_pairs_fail(tmp_path: Path) -> None:
    data = _fixture(tmp_path)
    rows = _rows(data["go_pairs_path"])
    _write(data["go_pairs_path"], list(rows[0]), [*rows, rows[0]])
    with pytest.raises(ValueError, match="Repeated go"):
        _call(data)


def test_cross_species_go_pair_fails(tmp_path: Path) -> None:
    data = _fixture(tmp_path)
    rows = _rows(data["go_pairs_path"])
    rows[0]["species_taxid"] = "200"
    _write(data["go_pairs_path"], list(rows[0]), rows)
    with pytest.raises(ValueError):
        _call(data)


def test_fragment_from_sequence_is_rejected(tmp_path: Path) -> None:
    data = _fixture(tmp_path)
    rows = _rows(data["go_pairs_path"])
    rows[0]["frag"] = "1"
    _write(data["go_pairs_path"], list(rows[0]), rows)
    with pytest.raises(ValueError, match="fragment"):
        _call(data)


def test_missing_target_taxonomy_is_rejected(tmp_path: Path) -> None:
    data = _fixture(tmp_path)
    tax_rows = _rows(data["target_taxonomy_path"])
    _write(data["target_taxonomy_path"], list(tax_rows[0]), tax_rows[:-1])
    data["contract_data"]["species_wide"]["target_taxonomy_sha256"] = (
        _sha(data["target_taxonomy_path"])
    )
    data["contract_data"]["reference"]["target_taxonomy_sha256"] = (
        _sha(data["target_taxonomy_path"])
    )
    data["scan_contract"].write_text(
        json.dumps(data["contract_data"]), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="domains differ"):
        _call(data)


def test_no_lineage_source_header_fails(tmp_path: Path) -> None:
    data = _fixture(tmp_path)
    data["lineage_path"].write_text("release\ttarget_id\n")
    with pytest.raises(ValueError, match="Missing columns"):
        _call(data)


def test_no_overwrite(tmp_path: Path) -> None:
    data = _fixture(tmp_path)
    _call(data)
    with pytest.raises(FileExistsError):
        _call(data)
