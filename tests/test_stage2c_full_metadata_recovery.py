"""Synthetic, no-release-scan contracts for pending FULL V2 metadata."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from probe.stage2c_evidence_bridge import REQUEST_FIELDS
from probe.stage2c_full_metadata_recovery import (
    METADATA_NAME,
    SEQUENCES_NAME,
    SUMMARY_NAME,
    preflight_full_recovery,
    recover_pending_full_metadata,
)
from probe.stage2c_metadata_reuse import REUSED_FIELDS
from probe.workflow.artifacts import Artifact, StageManifest


def _write_tsv(path: Path, fields, rows) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=fields, delimiter="\t", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)


def _read_tsv(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def _header(
    rid: int,
    *,
    acc: str | None = None,
    ox: str = "202",
    frag: str = "0",
    declared_len: int = 4,
) -> str:
    accession = acc if acc is not None else f"A{rid}"
    return (
        f">sp|{accession}|ENTRY{rid} RID={rid} SRC=1 REC={rid} "
        f"LINE=11 OX={ox} FRAG={frag} LEN={declared_len} "
        "PE=1 SV=2 SEC=B55 GENE=abc"
    )


def _request(rid: int) -> dict[str, str]:
    return {
        "release": "R1",
        "rid": str(rid),
        "candidate_accession": f"A{rid}",
        "candidate_raw_ox": "202",
        "candidate_resolved_taxid": "202",
        "species_taxid": "200",
        "full_record_number": str(rid),
        "target_reference_count": "2",
        "request_reason": "PINNED_FULL_V2_METADATA_VERIFICATION",
    }


def _fixture(tmp_path: Path, *, records: list[tuple[str, str]] | None = None):
    root = tmp_path / "full"
    root.mkdir()
    fasta = root / "01A_full_v2.fasta"
    if records is None:
        records = [(_header(n), "ACDE") for n in (1, 2, 3)]
    fasta.write_text(
        "".join(f"{header}\n{sequence}\n" for header, sequence in records),
        encoding="ascii",
    )
    digest = hashlib.sha256(fasta.read_bytes()).hexdigest()
    manifest = StageManifest(
        stage="01A_FULL",
        role="START",
        release_id="R1",
        parameters={},
        ontology=None,
        implementation={"name": "test-release-fasta-v2", "version": "1"},
        inputs=(),
        outputs=(Artifact("01A_full_v2.fasta", fasta.stat().st_size, digest),),
    )
    manifest.write(root / "manifest.json")
    requests = tmp_path / "pending.tsv"
    _write_tsv(requests, REQUEST_FIELDS, [_request(3), _request(1)])
    return requests, root / "manifest.json", tmp_path / "output"


def _run(args) -> dict:
    requests, manifest, destination = args
    return recover_pending_full_metadata(
        release="R1",
        pending_requests=requests,
        full_v2_manifest=manifest,
        output_dir=destination,
    )


def test_preflight_does_not_open_fasta_contents(tmp_path, monkeypatch):
    req, manifest, _ = _fixture(tmp_path)
    original_open = Path.open

    def guard(self, *args, **kwargs):
        if self.name == "01A_full_v2.fasta":
            raise AssertionError("Preflight must not open the FASTA")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guard)
    plan = preflight_full_recovery(
        release="R1", pending_requests=req, full_v2_manifest=manifest
    )
    assert plan["requested_unique_rids"] == 2
    assert plan["max_requested_rid"] == 3
    assert not plan["full_fasta_scanned"]


def test_recovers_unsorted_rids_in_full_ordinal_order(tmp_path: Path) -> None:
    args = _fixture(tmp_path)
    summary = _run(args)
    assert summary["counts"]["requested_unique_rids"] == 2
    assert summary["counts"]["full_v2_records_scanned"] == 3
    assert summary["counts"]["recovered_unique_rids"] == 2
    assert summary["full_fasta_sha256_verified"]
    assert not summary["taxonomy_verified_for_recovered_rows"]
    assert summary["go_donor_decisions"] == 0
    meta = _read_tsv(args[2] / METADATA_NAME)
    seq = _read_tsv(args[2] / SEQUENCES_NAME)
    assert list(meta[0]) == list(REUSED_FIELDS)
    assert [row["rid"] for row in meta] == ["1", "3"]
    assert [row["rid"] for row in seq] == ["1", "3"]
    assert meta[0]["entry"] == "ENTRY1"
    assert meta[0]["pe"] == "1"
    assert meta[0]["sv"] == "2"
    assert meta[0]["sec"] == "B55"
    assert meta[0]["gene"] == "abc"
    assert meta[0]["taxonomy_state"] == "REQUIRES_02B_REVALIDATION"
    assert meta[0]["sequence_sha256"] == hashlib.sha256(b"ACDE").hexdigest()
    assert seq[1]["sequence"] == "ACDE"
    assert json.loads((args[2] / SUMMARY_NAME).read_text()) == summary
    with pytest.raises(FileExistsError):
        _run(args)


def test_empty_requests_skips_expensive_scan(tmp_path, monkeypatch) -> None:
    req, manifest, output = _fixture(tmp_path)
    _write_tsv(req, REQUEST_FIELDS, [])
    original_open = Path.open

    def guard(self, *args, **kwargs):
        if self.name == "01A_full_v2.fasta":
            raise AssertionError("Empty request must not read FASTA")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guard)
    result = _run((req, manifest, output))
    assert not result["full_fasta_scanned"]
    assert not result["full_fasta_sha256_verified"]
    assert result["counts"]["recovered_unique_rids"] == 0
    assert _read_tsv(output / METADATA_NAME) == []


@pytest.mark.parametrize(
    ("field", "wrong"),
    [
        ("candidate_accession", "WRONG"),
        ("candidate_raw_ox", "201"),
        ("release", "OTHER"),
        ("full_record_number", "2"),
        ("request_reason", "UNKNOWN"),
        ("candidate_resolved_taxid", "0"),
        ("target_reference_count", ""),
    ],
)
def test_wrong_requests_fail_closed(tmp_path, field, wrong) -> None:
    req, manifest, output = _fixture(tmp_path)
    rows = _read_tsv(req)
    rows[0][field] = wrong
    _write_tsv(req, REQUEST_FIELDS, rows)
    with pytest.raises(ValueError):
        _run((req, manifest, output))
    assert not output.exists()


def test_duplicate_request_rejected(tmp_path: Path) -> None:
    req, manifest, output = _fixture(tmp_path)
    rows = _read_tsv(req)
    _write_tsv(req, REQUEST_FIELDS, [*rows, rows[0]])
    with pytest.raises(ValueError, match="Duplicate"):
        _run((req, manifest, output))
    assert not output.exists()


@pytest.mark.parametrize(
    ("mutator", "expected"),
    [
        (lambda records: records.__setitem__(0, (_header(2), "ACDE")), "ordinal"),
        (
            lambda records: records.__setitem__(2, (_header(3, frag="1"), "ACDE")),
            "conflict",
        ),
        (
            lambda records: records.__setitem__(2, (_header(3, ox="777"), "ACDE")),
            "conflict",
        ),
        (
            lambda records: records.__setitem__(
                2, (_header(3, declared_len=9), "ACDE")
            ),
            "sequence",
        ),
        (lambda records: records.__setitem__(2, (_header(3), "ACDX")), "sequence"),
    ],
)
def test_bad_full_record_rejected(tmp_path, mutator, expected) -> None:
    records = [(_header(n), "ACDE") for n in (1, 2, 3)]
    mutator(records)
    args = _fixture(tmp_path, records=records)
    with pytest.raises(ValueError, match=expected):
        _run(args)
    assert not args[2].exists()


def test_hash_mismatch_rejected_without_publication(tmp_path: Path) -> None:
    args = _fixture(tmp_path)
    fasta = args[1].parent / "01A_full_v2.fasta"
    raw = fasta.read_bytes().replace(b"ACDE\n", b"ACDF\n", 1)
    fasta.write_bytes(raw)
    with pytest.raises(ValueError, match="SHA256"):
        _run(args)
    assert not args[2].exists()


def test_missing_rid_blocks_publication(tmp_path: Path) -> None:
    req, manifest, output = _fixture(tmp_path)
    _write_tsv(req, REQUEST_FIELDS, [_request(4)])
    with pytest.raises(ValueError, match="does not exist"):
        _run((req, manifest, output))
    assert not output.exists()


def test_wrong_01a_release_is_rejected(tmp_path: Path) -> None:
    req, manifest, output = _fixture(tmp_path)
    with pytest.raises(ValueError, match="01A_FULL"):
        recover_pending_full_metadata(
            release="R2",
            pending_requests=req,
            full_v2_manifest=manifest,
            output_dir=output,
        )
    assert not output.exists()


def test_truncated_full_fasta_is_rejected(tmp_path: Path) -> None:
    args = _fixture(tmp_path)
    fasta = args[1].parent / "01A_full_v2.fasta"
    raw = fasta.read_bytes()
    fasta.write_bytes(raw[:-5])
    with pytest.raises(ValueError, match="size-mismatched"):
        _run(args)
    assert not args[2].exists()


def test_symlinked_requests_rejected(tmp_path: Path) -> None:
    req, manifest, output = _fixture(tmp_path)
    link = tmp_path / "linked.tsv"
    link.symlink_to(req)
    with pytest.raises(ValueError, match="symlinked"):
        recover_pending_full_metadata(
            release="R1",
            pending_requests=link,
            full_v2_manifest=manifest,
            output_dir=output,
        )
    assert not output.exists()
