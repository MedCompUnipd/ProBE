"""Synthetic recovery of FULL metadata from the frozen 05D store."""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path

import pytest

from probe.stage2c_evidence_bridge import REQUEST_FIELDS
from probe.stage2c_metadata_reuse import (
    REUSED_FIELDS,
    STORE_FIELDS,
    reuse_05d_metadata,
)


def _write(path: Path, fields, rows) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=fields, delimiter="\t", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)


def _read(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def _fixture(tmp_path: Path):
    requests = tmp_path / "requests.tsv"
    store = tmp_path / "05d.tsv"
    target = tmp_path / "reuse"
    requests_rows = []
    for rid in ("5", "8"):
        requests_rows.append(
            {
                "release": "R1",
                "rid": rid,
                "candidate_accession": f"A{rid}",
                "candidate_raw_ox": "202",
                "candidate_resolved_taxid": "202",
                "species_taxid": "200",
                "full_record_number": rid,
                "target_reference_count": "2",
                "request_reason": "PINNED_FULL_V2_METADATA_VERIFICATION",
            }
        )
    _write(requests, REQUEST_FIELDS, requests_rows)
    store_row = dict.fromkeys(STORE_FIELDS, "")
    store_row.update(
        release="R1",
        full_v2_record_number="5",
        rid="5",
        primary_accession="A5",
        entry="TEST5",
        raw_ox="202",
        frag="0",
        sequence_length="4",
        pe="1",
        sv="2",
        sec="A55",
        gene="xyz",
        sequence="ACDE",
    )
    _write(store, STORE_FIELDS, [store_row])
    return requests, store, target


def test_reuses_exact_available_and_preserves_pending(tmp_path: Path) -> None:
    requests, store, target = _fixture(tmp_path)
    result = reuse_05d_metadata(
        release="R1",
        requests=requests,
        store_05d=store,
        output_dir=target,
    )
    assert result["counts"]["requested_unique_rids"] == 2
    assert result["counts"]["reused_unique_rids"] == 1
    assert result["counts"]["pending_unique_rids"] == 1
    assert not result["full_uniprot_fasta_scanned"]
    assert result["go_donor_decisions"] == 0
    reused = _read(target / "2C_reused_full_metadata.tsv")
    pending = _read(target / "2C_pending_full_metadata.tsv")
    assert list(reused[0]) == list(REUSED_FIELDS)
    assert reused[0]["rid"] == "5"
    assert reused[0]["sequence_sha256"] == hashlib.sha256(b"ACDE").hexdigest()
    assert pending[0]["rid"] == "8"
    with pytest.raises(FileExistsError):
        reuse_05d_metadata(
            release="R1",
            requests=requests,
            store_05d=store,
            output_dir=target,
        )


def test_conflicting_accession_is_not_published(tmp_path: Path) -> None:
    requests, store, target = _fixture(tmp_path)
    rows = _read(store)
    rows[0]["primary_accession"] = "WRONG"
    _write(store, STORE_FIELDS, rows)
    with pytest.raises(ValueError, match="conflicts"):
        reuse_05d_metadata(
            release="R1",
            requests=requests,
            store_05d=store,
            output_dir=target,
        )
    assert not target.exists()


def test_wrong_sequence_length_is_not_published(tmp_path: Path) -> None:
    requests, store, target = _fixture(tmp_path)
    rows = _read(store)
    rows[0]["sequence_length"] = "5"
    _write(store, STORE_FIELDS, rows)
    with pytest.raises(ValueError, match="length"):
        reuse_05d_metadata(
            release="R1",
            requests=requests,
            store_05d=store,
            output_dir=target,
        )
    assert not target.exists()


def test_duplicate_request_refused(tmp_path: Path) -> None:
    requests, store, target = _fixture(tmp_path)
    rows = _read(requests)
    _write(requests, REQUEST_FIELDS, [*rows, rows[0]])
    with pytest.raises(ValueError, match="Duplicate"):
        reuse_05d_metadata(
            release="R1",
            requests=requests,
            store_05d=store,
            output_dir=target,
        )
    assert not target.exists()


def test_fragment_cannot_be_promoted(tmp_path: Path) -> None:
    requests, store, target = _fixture(tmp_path)
    rows = _read(store)
    rows[0]["frag"] = "1"
    _write(store, STORE_FIELDS, rows)
    with pytest.raises(ValueError, match="conflicts"):
        reuse_05d_metadata(
            release="R1",
            requests=requests,
            store_05d=store,
            output_dir=target,
        )
    assert not target.exists()


def test_missing_store_row_is_pending(tmp_path: Path) -> None:
    requests, store, target = _fixture(tmp_path)
    _write(store, STORE_FIELDS, [])
    result = reuse_05d_metadata(
        release="R1",
        requests=requests,
        store_05d=store,
        output_dir=target,
    )
    assert result["counts"]["pending_unique_rids"] == 2
    assert _read(target / "2C_reused_full_metadata.tsv") == []


def test_store_from_different_release_is_refused(tmp_path: Path) -> None:
    requests, store, target = _fixture(tmp_path)
    rows = _read(store)
    rows[0]["release"] = "R2"
    _write(store, STORE_FIELDS, rows)
    with pytest.raises(ValueError, match="release mismatch"):
        reuse_05d_metadata(
            release="R1",
            requests=requests,
            store_05d=store,
            output_dir=target,
        )
    assert not target.exists()
