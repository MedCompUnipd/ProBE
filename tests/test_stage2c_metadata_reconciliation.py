"""Synthetic end-to-end contracts for FULL metadata reconciliation."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from probe.stage2c_evidence_bridge import REQUEST_FIELDS
from probe.stage2c_full_metadata_recovery import recover_pending_full_metadata
from probe.stage2c_metadata_reconciliation import (
    METADATA_OUTPUT,
    SEQUENCES_OUTPUT,
    SUMMARY_OUTPUT,
    reconcile_full_metadata,
)
from probe.stage2c_metadata_reuse import (
    STORE_FIELDS,
    reuse_05d_metadata,
)
from probe.workflow.artifacts import Artifact, StageManifest


def _write(path: Path, fields, rows) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=fields, delimiter="\t", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def _request(rid: str) -> dict[str, str]:
    return {
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


def _store_record(rid: str) -> dict[str, str]:
    row = dict.fromkeys(STORE_FIELDS, "")
    row.update(
        release="R1",
        full_v2_record_number=rid,
        rid=rid,
        primary_accession=f"A{rid}",
        entry=f"ENTRY{rid}",
        raw_ox="202",
        frag="0",
        sequence_length="4",
        pe="1",
        sv="2",
        sec="B55",
        gene="abc",
        sequence="ACDE",
    )
    return row


def _manifest(tmp: Path) -> Path:
    directory = tmp / "full"
    directory.mkdir()
    fasta = directory / "01A_full_v2.fasta"
    with fasta.open("w", encoding="ascii") as stream:
        for n in range(1, 4):
            stream.write(
                f">sp|A{n}|ENTRY{n} RID={n} SRC=1 REC={n} "
                "LINE=11 OX=202 FRAG=0 LEN=4 PE=1 SV=2 "
                "SEC=B55 GENE=abc\nACDE\n"
            )
    manifest = StageManifest(
        stage="01A_FULL",
        role="START",
        release_id="R1",
        parameters={},
        ontology=None,
        implementation={"name": "synthetic", "version": "1"},
        inputs=(),
        outputs=(
            Artifact(
                "01A_full_v2.fasta",
                fasta.stat().st_size,
                hashlib.sha256(fasta.read_bytes()).hexdigest(),
            ),
        ),
    )
    manifest.write(directory / "manifest.json")
    return directory / "manifest.json"


def _fixture(tmp_path: Path, *, reused: bool = True, pending: bool = True):
    requests = tmp_path / "requests.tsv"
    store = tmp_path / "05d.tsv"
    universe = [*(["1"] if reused else []), *(["3"] if pending else [])]
    _write(requests, REQUEST_FIELDS, [_request(rid) for rid in universe])
    _write(store, STORE_FIELDS, [_store_record("1")] if reused else [])
    reuse = tmp_path / "reuse"
    reuse_05d_metadata(
        release="R1",
        requests=requests,
        store_05d=store,
        output_dir=reuse,
    )
    recovery = tmp_path / "recovery"
    if pending:
        recover_pending_full_metadata(
            release="R1",
            pending_requests=reuse / "2C_pending_full_metadata.tsv",
            full_v2_manifest=_manifest(tmp_path),
            output_dir=recovery,
        )
    return requests, store, reuse, recovery if pending else None, tmp_path / "out"


def _run(paths) -> dict:
    requests, store, reuse, recovery, output = paths
    return reconcile_full_metadata(
        release="R1",
        requests=requests,
        reuse_dir=reuse,
        canonical_05d_store=store,
        recovery_dir=recovery,
        output_dir=output,
    )


def test_reconciles_two_channels_exactly_once(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    result = _run(paths)
    counts = result["counts"]
    assert counts["requested_unique_rids"] == 2
    assert counts["reused_unique_rids"] == 1
    assert counts["recovered_unique_rids"] == 1
    assert counts["reused_sequences"] == 1
    assert counts["recovered_sequences"] == 1
    assert result["go_donor_decisions"] == 0
    assert result["taxonomy_verified"] is False
    assert result["complete_stage05a2_replacement"] is False
    rows = _rows(paths[-1] / METADATA_OUTPUT)
    seq = _rows(paths[-1] / SEQUENCES_OUTPUT)
    assert [row["rid"] for row in rows] == ["1", "3"]
    assert [row["rid"] for row in seq] == ["1", "3"]
    assert [row["source_state"] for row in seq] == [
        "CANONICAL_05D_METADATA_REUSED",
        "PINNED_FULL_V2_METADATA_RECOVERED",
    ]
    assert all(row["sequence"] == "ACDE" for row in seq)
    assert json.loads((paths[-1] / SUMMARY_OUTPUT).read_text()) == result
    assert len(result["output_sha256"][METADATA_OUTPUT]) == 64
    with pytest.raises(FileExistsError):
        _run(paths)


def test_all_reused_does_not_need_full_scan(tmp_path: Path) -> None:
    paths = _fixture(tmp_path, pending=False)
    result = _run(paths)
    assert result["counts"]["recovered_unique_rids"] == 0
    assert len(_rows(paths[-1] / METADATA_OUTPUT)) == 1


def test_all_recovered_without_05d_rows(tmp_path: Path) -> None:
    paths = _fixture(tmp_path, reused=False)
    result = _run(paths)
    assert result["counts"]["reused_unique_rids"] == 0
    assert len(_rows(paths[-1] / SEQUENCES_OUTPUT)) == 1


def test_empty_universe_is_supported(tmp_path: Path) -> None:
    paths = _fixture(tmp_path, reused=False, pending=False)
    result = _run(paths)
    assert result["counts"]["requested_unique_rids"] == 0
    assert _rows(paths[-1] / METADATA_OUTPUT) == []


def test_missing_recovery_fails_without_partial_publication(tmp_path: Path):
    paths = list(_fixture(tmp_path))
    paths[3] = None
    with pytest.raises(ValueError, match="Missing recovery"):
        _run(paths)
    assert not paths[-1].exists()


def test_mutated_reused_sequence_detected(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    rows = _rows(paths[1])
    rows[0]["sequence"] = "ACDF"
    _write(paths[1], STORE_FIELDS, rows)
    with pytest.raises(ValueError, match="sequence conflict"):
        _run(paths)
    assert not paths[-1].exists()


def test_mutated_recovered_sequence_detected(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    sequence_path = paths[3] / "2C_recovered_full_sequences.tsv"
    rows = _rows(sequence_path)
    rows[0]["sequence"] = "ACDF"
    _write(sequence_path, tuple(rows[0]), rows)
    with pytest.raises(ValueError, match="sequence conflict"):
        _run(paths)
    assert not paths[-1].exists()


def test_overlap_is_rejected(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    meta = paths[3] / "2C_recovered_full_metadata.tsv"
    rows = _rows(meta)
    rows[0]["rid"] = "1"
    rows[0]["source_full_v2_record_number"] = "1"
    _write(meta, tuple(rows[0]), rows)
    with pytest.raises(ValueError, match="overlapping"):
        _run(paths)
    assert not paths[-1].exists()


def test_duplicate_recovered_sequence_is_rejected(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    sequence_path = paths[3] / "2C_recovered_full_sequences.tsv"
    rows = _rows(sequence_path)
    _write(sequence_path, tuple(rows[0]), [*rows, rows[0]])
    with pytest.raises(ValueError, match="duplicate"):
        _run(paths)
    assert not paths[-1].exists()


def test_wrong_provenance_fails_closed(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    meta = paths[3] / "2C_recovered_full_metadata.tsv"
    rows = _rows(meta)
    rows[0]["taxonomy_state"] = "VERIFIED"
    _write(meta, tuple(rows[0]), rows)
    with pytest.raises(ValueError, match="provenance"):
        _run(paths)
    assert not paths[-1].exists()


def test_source_summary_count_mismatch_rejected(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    summary = paths[2] / "2C_full_metadata_reuse.summary.json"
    data = json.loads(summary.read_text())
    data["counts"]["pending_unique_rids"] = 0
    summary.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="count mismatch"):
        _run(paths)
    assert not paths[-1].exists()


def test_original_requests_are_authoritative(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    rows = _rows(paths[0])
    rows[0]["candidate_raw_ox"] = "999"
    _write(paths[0], REQUEST_FIELDS, rows)
    with pytest.raises(ValueError, match="conflict"):
        _run(paths)
    assert not paths[-1].exists()


def test_symlinked_destination_rejected(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    elsewhere = tmp_path / "other"
    elsewhere.mkdir()
    paths[-1].symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(FileExistsError):
        _run(paths)
    assert list(elsewhere.iterdir()) == []
