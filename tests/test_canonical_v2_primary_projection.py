"""Tests for a 05A2-independent and evidence-conserving primary fact union."""

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
    RESCUE_REQUIRED,
)
from probe.canonical_v2.contracts import (
    OUTPUT_NAME as LEDGER_NAME,
)
from probe.canonical_v2.contracts import (
    SUMMARY_NAME as LEDGER_SUMMARY,
)
from probe.canonical_v2.primary_projection import (
    OUTPUT_NAME,
    SUMMARY_NAME,
    project_primary_candidate_facts,
)


def _write(path: Path, fields: frozenset[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=sorted(fields), delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fields})


def _go_source(rid: str, accession: str) -> dict:
    row = {field: "1" for field in GO_SOURCE_REQUIRED}
    row.update(
        target_id="T", rid=rid, accession=accession, raw_ox="111",
        resolved_taxid="111", species_taxid="100", frag="0",
        record_number="1", target_bucket="S:100", exact_sequence="0",
        candidate_length="15", target_length="15",
    )
    return row


def _lineage_source(rid: str, accession: str, frag: str) -> dict:
    row = {field: "1" for field in LINEAGE_REQUIRED}
    row.update(
        release="R1", target_id="T", rid=rid, record_number=rid,
        candidate_primary_accession=accession, raw_ox="111",
        resolved_taxid="111", species_taxid="100", frag=frag,
        sequence_length="15" if rid == "101" else "1",
        target_primary_bucket="S:100",
        lineage_fact_signature="DOCUMENTARY", target_accession_keys="KEY",
    )
    return row


def _discovery(rid: str, accession: str, taxid: str, full: bool) -> dict:
    row = {field: "" for field in DISCOVERY_REQUIRED}
    row.update(
        release="R1", target_id="T", rid=rid,
        candidate_accession=accession, candidate_raw_ox=taxid,
        target_resolved_taxid="111", candidate_resolved_taxid=taxid,
        species_taxid="100", go_positive_species_wide=str(int(not full)),
        full_exact_species_wide=str(int(full)), go_positive_exact="0",
        go_positive_record_number="" if full else "1",
        full_record_number="9" if full else "",
        strict_gate="DENIED" if full else "DEFER_TO_DOCUMENTARY",
        same_species_exact_gate=(
            "ELIGIBLE" if full else "DEFER_TO_DOCUMENTARY"
        ),
    )
    return row


def _start(tmp_path: Path, *, rescue_overlap: bool = False) -> Path:
    full = {field: "1" for field in FULL_SOURCE_REQUIRED}
    full.update(
        release="R1", target_id="T", rid="102", record_number="9",
        accession="A102", raw_ox="112", target_resolved_taxid="111",
        candidate_resolved_taxid="112", species_taxid="100", frag="0",
        exact_full_sequence="1", discovery_channel="FULL_V2_EXACT",
    )
    rescue = _go_source("104", "A104")
    rescue["record_number"] = "104"
    rescue_rows = [rescue]
    if rescue_overlap:
        overlap = _go_source("101", "A101")
        overlap["record_number"] = "101"
        rescue_rows.append(overlap)
    sources = {
        "discovery_union": (
            DISCOVERY_REQUIRED,
            [
                _discovery("101", "A101", "111", full=False),
                _discovery("102", "A102", "112", full=True),
            ],
        ),
        "go_positive_pairs": (
            GO_SOURCE_REQUIRED, [_go_source("101", "A101")]
        ),
        "full_exact_pairs": (FULL_SOURCE_REQUIRED, [full]),
        "lineage_pairs": (
            LINEAGE_REQUIRED,
            [
                _lineage_source("101", "A101", "0"),
                _lineage_source("103", "A103", "1"),
            ],
        ),
        "rescue_pairs": (RESCUE_REQUIRED, rescue_rows),
    }
    paths = {}
    for name, (fields, rows) in sources.items():
        path = tmp_path / f"{name}.tsv"
        _write(path, fields, rows)
        paths[name] = path
    assemble_candidate_provenance(
        release="R1", output_dir=tmp_path / "published", **paths
    )
    return tmp_path / "published"


def _project(tmp_path: Path, ledger: Path | None = None) -> dict:
    return project_primary_candidate_facts(
        release="R1", ledger_dir=ledger or tmp_path / "published",
        output_dir=tmp_path / "primary",
    )


def _rows(tmp_path: Path) -> dict[str, dict[str, str]]:
    with (tmp_path / "primary" / OUTPUT_NAME).open() as stream:
        return {
            row["rid"]: row for row in csv.DictReader(stream, delimiter="\t")
        }


def _change_ledger(tmp_path: Path, change) -> None:
    ledger = tmp_path / "published"
    path = ledger / LEDGER_NAME
    with path.open() as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        fields = reader.fieldnames
        assert fields is not None
        rows = list(reader)
    change(rows)
    with path.open("w") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=fields, delimiter="\t", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)
    summary_path = ledger / LEDGER_SUMMARY
    summary = json.loads(summary_path.read_text())
    summary["output_sha256"][LEDGER_NAME] = hashlib.sha256(
        path.read_bytes()
    ).hexdigest()
    summary_path.write_text(json.dumps(summary) + "\n")


def test_four_channels_and_documentary_exact_coexist(tmp_path: Path) -> None:
    _start(tmp_path)
    summary = _project(tmp_path)
    rows = _rows(tmp_path)
    assert set(rows) == {"101", "102", "103", "104"}
    assert summary["ready_for_06a"] is False
    assert summary["donor_decisions"] == summary["dp_decisions"] == 0
    assert rows["101"]["discovery_channel"] == "BOTH"
    assert rows["101"]["lineage_discovered"] == "1"
    assert rows["101"]["sequence_discovered"] == "1"
    assert rows["101"]["go_source_json"]
    assert rows["101"]["lineage_source_json"]
    assert rows["102"]["seq_exact_sequence"] == "1"
    assert rows["102"]["sequence_search_record_number"] == ""
    assert rows["102"]["full_v2_record_number"] == "9"
    assert rows["103"]["discovery_channel"] == "LINEAGE_ONLY"
    assert rows["103"]["frag"] == "1"
    assert rows["103"]["seq_exact_sequence"] == ""
    assert rows["104"]["full_v2_rescue_sequence_discovered"] == "1"
    assert rows["104"]["seq_exact_sequence"] == "0"


def test_full_exact_no_invented_metrics(tmp_path: Path) -> None:
    _start(tmp_path)
    _project(tmp_path)
    full = _rows(tmp_path)["102"]
    assert full["metadata_source"] == "FULL_ORDINAL_ONLY"
    assert full["sequence_evidence_state"] == "FULL_EXACT_UNMATERIALIZED"
    assert full["seq_exact_sequence"] == "1"
    assert full["seq_shared_distinct_kmers"] == ""
    assert full["entry"] == full["gene"] == full["pe"] == ""
    assert full["requires_full_sequence_materialization"] == "1"


def test_lineage_keeps_all_04e_fields(tmp_path: Path) -> None:
    _start(tmp_path)
    _project(tmp_path)
    row = _rows(tmp_path)["103"]
    source = json.loads(row["lineage_source_json"])
    for name in (
        "lineage_evidence_row_count", "target_accession_keys",
        "matched_typed_keys", "collision_keys", "source_roles",
    ):
        assert row[f"lin_{name}"] == source[name]


def test_summary_has_b1_custody(tmp_path: Path) -> None:
    ledger = _start(tmp_path)
    summary = _project(tmp_path)
    assert summary["input_sha256"][LEDGER_NAME] == hashlib.sha256(
        (ledger / LEDGER_NAME).read_bytes()
    ).hexdigest()
    assert summary["input_sha256"][LEDGER_SUMMARY] == hashlib.sha256(
        (ledger / LEDGER_SUMMARY).read_bytes()
    ).hexdigest()
    assert summary["output_sha256"][OUTPUT_NAME] == hashlib.sha256(
        (tmp_path / "primary" / OUTPUT_NAME).read_bytes()
    ).hexdigest()
    assert (tmp_path / "primary" / SUMMARY_NAME).is_file()


def test_tampered_ledger_rejected(tmp_path: Path) -> None:
    ledger = _start(tmp_path)
    with (ledger / LEDGER_NAME).open("a") as stream:
        stream.write("unexpected\n")
    with pytest.raises(ValueError, match="checksum"):
        _project(tmp_path)
    assert not (tmp_path / "primary").exists()


def test_tampered_raw_source_rejected_even_with_updated_hash(
    tmp_path: Path,
) -> None:
    _start(tmp_path)

    def modify(rows):
        source = json.loads(rows[0]["go_source_json"])
        source["accession"] = "WRONG"
        rows[0]["go_source_json"] = json.dumps(source)

    _change_ledger(tmp_path, modify)
    with pytest.raises(ValueError, match="accession disagreement"):
        _project(tmp_path)
    assert not (tmp_path / "primary").exists()


def test_missing_lineage_flag_rejected_even_with_updated_hash(
    tmp_path: Path,
) -> None:
    _start(tmp_path)
    _change_ledger(tmp_path, lambda rows: rows[0].update(
        lineage_discovered="0"
    ))
    with pytest.raises(ValueError, match="Flag/payload disagreement"):
        _project(tmp_path)


def test_unsorted_or_duplicate_key_fails(tmp_path: Path) -> None:
    _start(tmp_path)
    _change_ledger(tmp_path, lambda rows: rows.reverse())
    with pytest.raises(ValueError, match="sorted and unique"):
        _project(tmp_path)


def test_no_overwrite(tmp_path: Path) -> None:
    _start(tmp_path)
    _project(tmp_path)
    with pytest.raises(FileExistsError, match="destination"):
        _project(tmp_path)


def test_no_legacy_stage_needed(tmp_path: Path) -> None:
    _start(tmp_path)
    assert not any(tmp_path.glob("*05A2*"))
    assert not any(tmp_path.glob("*06C1*"))
    _project(tmp_path)
    assert _rows(tmp_path)


def test_release_mismatch_fail_closed(tmp_path: Path) -> None:
    ledger = _start(tmp_path)
    with pytest.raises(ValueError, match="schema mismatch"):
        project_primary_candidate_facts(
            release="R2", ledger_dir=ledger,
            output_dir=tmp_path / "primary",
        )


def test_symlink_input_rejected(tmp_path: Path) -> None:
    ledger = _start(tmp_path)
    original = ledger / LEDGER_NAME
    original.rename(ledger / "original.tsv")
    original.symlink_to(ledger / "original.tsv")
    with pytest.raises(ValueError, match="unsafe"):
        _project(tmp_path)


def test_lineage_only_exactness_not_guessed(tmp_path: Path) -> None:
    _start(tmp_path)
    _project(tmp_path)
    row = _rows(tmp_path)["103"]
    assert row["sequence_discovered"] == "0"
    assert row["seq_exact_sequence"] == ""
    assert row["strict_gate"] == ""
    assert row["same_species_exact_gate"] == ""


def test_conflicting_full_ordinals_kept_without_false_resolution(
    tmp_path: Path,
) -> None:
    _start(tmp_path)

    def modify(rows):
        full = rows[1]
        assert full["rid"] == "102"
        from_04e = json.loads(rows[0]["lineage_source_json"])
        from_04e.update(
            rid="102", record_number="102",
            candidate_primary_accession="A102", raw_ox="112",
            resolved_taxid="112", frag="0",
            sequence_length="1",
        )
        full["lineage_discovered"] = "1"
        full["lineage_source_json"] = json.dumps(from_04e)

    _change_ledger(tmp_path, modify)
    summary_path = tmp_path / "published" / LEDGER_SUMMARY
    summary = json.loads(summary_path.read_text())
    summary["counts"]["lineage_discovered"] += 1
    summary_path.write_text(json.dumps(summary) + "\n")
    report = _project(tmp_path)
    full = _rows(tmp_path)["102"]
    assert full["full_ordinal_state"] == "CONFLICT"
    assert full["full_v2_record_number"] == ""
    assert full["full_exact_record_number"] == "9"
    assert full["lineage_full_record_number"] == "102"
    assert report["counts"]["full_ordinal_conflicts"] == 1
    assert report["ready_for_06a"] is False


def test_rescue_overlap_keeps_both_sources(tmp_path: Path) -> None:
    _start(tmp_path, rescue_overlap=True)
    report = _project(tmp_path)
    row = _rows(tmp_path)["101"]
    assert row["go_source_json"]
    assert row["rescue_source_json"]
    assert row["lineage_source_json"]
    assert row["seq_exact_sequence"] == "0"
    assert row["sequence_search_record_number"] == "1"
    assert row["rescue_full_record_number"] == "101"
    assert report["counts"]["unique_pairs"] == 4
    assert report["counts"]["rescue_pairs"] == 2


def test_go_scanner_ordinal_tamper_rejected(tmp_path: Path) -> None:
    _start(tmp_path)
    _change_ledger(tmp_path, lambda rows: rows[0].update(
        go_positive_record_number="222"
    ))
    with pytest.raises(ValueError, match="GO scanner exactness/ordinal"):
        _project(tmp_path)
