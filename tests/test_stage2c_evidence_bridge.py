"""Safety tests for Stage 2C candidate evidence metadata bridge."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from probe.stage2c_candidate_delta import DELTA_COLUMNS
from probe.stage2c_candidate_union import FULL_REQUIRED, GO_REQUIRED
from probe.stage2c_evidence_bridge import (
    BRIDGE_FIELDS,
    GO_FACTS,
    GO_SEQ,
    REQUEST_FIELDS,
    prepare_evidence_bridge,
)


def _write(path: Path, fields, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=fields, delimiter="\t", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)


def _read(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def _delta(target: str, rid: str, *, go: bool, full: bool) -> dict:
    row = dict.fromkeys(DELTA_COLUMNS, "")
    row.update(
        release="R1",
        target_id=target,
        rid=rid,
        candidate_accession=f"A{rid}",
        candidate_raw_ox="202",
        target_resolved_taxid="201",
        candidate_resolved_taxid="202",
        species_taxid="200",
        go_positive_species_wide=str(int(go)),
        full_exact_species_wide=str(int(full)),
        go_positive_exact=str(int(go)),
        go_positive_record_number=rid if go else "",
        full_record_number=rid if full else "",
        strict_gate="DENIED",
        same_species_exact_gate="ELIGIBLE" if (go or full) else "DENIED",
        integration_state="NEEDS_EVIDENCE_RECONSTRUCTION",
    )
    return row


def _go(row: dict) -> dict:
    result = dict.fromkeys(set(GO_REQUIRED) | set(GO_FACTS) | set(GO_SEQ), "0")
    result.update(
        target_id=row["target_id"],
        rid=row["rid"],
        accession=row["candidate_accession"],
        raw_ox=row["candidate_raw_ox"],
        resolved_taxid=row["candidate_resolved_taxid"],
        species_taxid=row["species_taxid"],
        frag="0",
        record_number=row["go_positive_record_number"],
        exact_sequence=row["go_positive_exact"],
        candidate_length="30",
        target_length="30",
        target_bucket="200",
        entry="PROT_TEST",
        pe="2",
        sv="1",
        sec="",
        gene="gX",
    )
    return result


def _full(row: dict) -> dict:
    result = dict.fromkeys(FULL_REQUIRED, "")
    result.update(
        release="R1",
        target_id=row["target_id"],
        record_number=row["full_record_number"],
        rid=row["rid"],
        accession=row["candidate_accession"],
        raw_ox=row["candidate_raw_ox"],
        target_resolved_taxid=row["target_resolved_taxid"],
        candidate_resolved_taxid=row["candidate_resolved_taxid"],
        species_taxid=row["species_taxid"],
        frag="0",
        length_aa="30",
        exact_full_sequence="1",
        discovery_channel="FULL_V2_EXACT",
    )
    return result


def _fixture(tmp_path: Path):
    delta, go, full, output = (
        tmp_path / "delta.tsv",
        tmp_path / "go.tsv",
        tmp_path / "full.tsv",
        tmp_path / "bundle",
    )
    a = _delta("T1", "101", go=True, full=False)
    b = _delta("T2", "102", go=False, full=True)
    c = _delta("T3", "102", go=False, full=True)
    d = _delta("T4", "103", go=True, full=True)
    _write(delta, DELTA_COLUMNS, [a, b, c, d])
    _write(
        go,
        sorted(set(GO_REQUIRED) | set(GO_FACTS) | set(GO_SEQ)),
        [
            _go(a),
            _go(d),
        ],
    )
    _write(full, sorted(FULL_REQUIRED), [_full(b), _full(c), _full(d)])
    return delta, go, full, output


def _run(paths):
    delta, go, full, output = paths
    return prepare_evidence_bridge(
        release="R1",
        delta=delta,
        go_positive_pairs=go,
        full_exact_pairs=full,
        output_dir=output,
    )


def test_metadata_bridge_and_unique_requests(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    summary = _run(paths)
    assert summary["counts"]["delta_candidates"] == 4
    assert summary["counts"]["go_metadata_verified"] == 2
    assert summary["counts"]["full_metadata_pending"] == 2
    assert summary["counts"]["unique_full_record_requests"] == 2
    assert summary["go_donor_decisions"] == 0
    assert summary["complete_stage05a2_replacement"] is False
    bridge = _read(paths[3] / "2C_evidence_bridge.tsv")
    assert list(bridge[0]) == list(BRIDGE_FIELDS)
    records = {row["target_id"]: row for row in bridge}
    assert records["T1"]["entry"] == "PROT_TEST"
    assert records["T1"]["seq_exact_sequence"] == "1"
    assert records["T2"]["entry"] == ""
    assert records["T2"]["seq_exact_sequence"] == ""
    assert records["T2"]["evidence_state"] == "FULL_RECORD_LOOKUP_REQUIRED"
    requests = _read(paths[3] / "2C_full_record_requests.tsv")
    assert list(requests[0]) == list(REQUEST_FIELDS)
    assert len(requests) == 2
    assert {row["rid"]: row["target_reference_count"] for row in requests} == {
        "102": "2",
        "103": "1",
    }
    assert (
        json.loads((paths[3] / "2C_evidence_bridge.summary.json").read_text())[
            "full_fasta_scanned"
        ]
        is False
    )


def test_existing_output_refused(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    _run(paths)
    with pytest.raises(FileExistsError):
        _run(paths)


def test_missing_scanner_record_refused_without_publication(tmp_path: Path):
    paths = _fixture(tmp_path)
    _write(paths[2], sorted(FULL_REQUIRED), [])
    with pytest.raises(ValueError, match="Missing or unexpected FULL"):
        _run(paths)
    assert not paths[3].exists()


def test_metadata_conflict_refused(tmp_path: Path):
    paths = _fixture(tmp_path)
    rows = _read(paths[1])
    rows[0]["accession"] = "INCORRECT"
    _write(paths[1], rows[0], rows)
    with pytest.raises(ValueError, match="contradicts"):
        _run(paths)
    assert not paths[3].exists()


def test_duplicate_scanner_row_refused(tmp_path: Path):
    paths = _fixture(tmp_path)
    rows = _read(paths[1])
    _write(paths[1], rows[0], [*rows, rows[0]])
    with pytest.raises(ValueError, match="Duplicate scanner"):
        _run(paths)


def test_noncanonical_rid_refused(tmp_path: Path):
    paths = _fixture(tmp_path)
    rows = _read(paths[0])
    rows[0]["rid"] = "0101"
    _write(paths[0], rows[0], rows)
    with pytest.raises(ValueError, match="Noncanonical RID"):
        _run(paths)


def test_fragment_refused(tmp_path: Path):
    paths = _fixture(tmp_path)
    rows = _read(paths[1])
    rows[0]["frag"] = "1"
    _write(paths[1], rows[0], rows)
    with pytest.raises(ValueError, match="Fragment"):
        _run(paths)


def test_taxonomic_metadata_conflict_on_shared_rid(tmp_path: Path):
    paths = _fixture(tmp_path)
    rows = _read(paths[0])
    rows[2]["candidate_accession"] = "CONFLICT"
    _write(paths[0], rows[0], rows)
    full = _read(paths[2])
    full[1]["accession"] = "CONFLICT"
    _write(paths[2], full[0], full)
    with pytest.raises(ValueError, match="Conflicting FULL record"):
        _run(paths)
