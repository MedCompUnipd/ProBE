"""Stage 2C GO-positive sequence recovery: release and byte-level contracts."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from probe.stage2c_go_sequence_recovery import (
    ANNEX_NAME,
    SUMMARY_NAME,
    preflight_pending_go,
    recover_pending_go,
)
from probe.stage2c_sequence_coverage import (
    COVERAGE_NAME,
    PENDING_FIELDS,
    PENDING_NAME,
    SUMMARY_NAME_OUT,
)
from probe.workflow.artifacts import atomic_stage_bundle


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, fields, rows) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=fields, delimiter="\t", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)


def _records(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def _fixture(tmp: Path, *, empty: bool = False, first_sequence: bytes = b"ACDE"):
    coverage = tmp / "coverage"
    coverage.mkdir()
    pending = (
        []
        if empty
        else [
            {
                "release": "R1",
                "rid": "50",
                "candidate_accession": "A50",
                "raw_ox": "202",
                "go_positive_record_number": "1",
                "new_primary_reference_count": "2",
                "reason": "GO_POSITIVE_SEQUENCE_NOT_MATERIALIZED",
            },
            {
                "release": "R1",
                "rid": "10",
                "candidate_accession": "A10",
                "raw_ox": "202",
                "go_positive_record_number": "3",
                "new_primary_reference_count": "1",
                "reason": "GO_POSITIVE_SEQUENCE_NOT_MATERIALIZED",
            },
        ]
    )
    _write(coverage / PENDING_NAME, PENDING_FIELDS, pending)
    (coverage / COVERAGE_NAME).write_text("release\trid\nR1\t50\n", encoding="utf-8")
    summary = {
        "schema": "stage2c-sequence-coverage-v1",
        "release": "R1",
        "go_donor_decisions": 0,
        "output_sha256": {
            name: _sha(coverage / name) for name in (COVERAGE_NAME, PENDING_NAME)
        },
    }
    (coverage / SUMMARY_NAME_OUT).write_text(json.dumps(summary))
    manifest = tmp / "01c" / "manifest.json"
    fasta = (
        b">sp|A50|ENTRY50 RID=50 OX=202 FRAG=0 LEN=4 PE=2 SV=1 "
        b"GENE=g50\n" + first_sequence + b"\n"
        b">tr|A9|ENTRY9 RID=9 OX=303 FRAG=1 LEN=4 PE=3 SV=2\nACDF\n"
        b">sp|A10|ENTRY10 RID=10 OX=202 FRAG=0 LEN=4 PE=1 SV=1\nACDG\n"
    )
    with atomic_stage_bundle(
        manifest.parent,
        stage="01C",
        role="START",
        release_id="R1",
        parameters={},
        ontology=None,
        implementation={"name": "test-01c", "version": "1"},
    ) as stage:
        (stage / "01C_go_positive_v2.fasta").write_bytes(fasta)
    return coverage, manifest, tmp / "out"


def _run(paths):
    coverage, manifest, output = paths
    return recover_pending_go(
        release="R1",
        coverage_dir=coverage,
        go_manifest=manifest,
        output_dir=output,
    )


def _refresh(coverage: Path) -> None:
    file = coverage / SUMMARY_NAME_OUT
    summary = json.loads(file.read_text())
    for name in (COVERAGE_NAME, PENDING_NAME):
        summary["output_sha256"][name] = _sha(coverage / name)
    file.write_text(json.dumps(summary))


def test_go_ordinals_distinct_from_full_rids(tmp_path):
    paths = _fixture(tmp_path)
    summary = _run(paths)
    rows = _records(paths[-1] / ANNEX_NAME)
    assert [item["rid"] for item in rows] == ["50", "10"]
    assert [item["sequence"] for item in rows] == ["ACDE", "ACDG"]
    assert rows[0]["gene"] == "g50"
    assert rows[0]["sequence_sha256"] == hashlib.sha256(b"ACDE").hexdigest()
    assert summary["counts"]["recovered_unique_rids"] == 2
    assert summary["counts"]["fasta_records_scanned"] == 3
    assert summary["go_fasta_sha256_verified"] is True
    assert summary["go_donor_decisions"] == 0
    assert json.loads((paths[-1] / SUMMARY_NAME).read_text()) == summary


@pytest.mark.parametrize("residue", ["X", "B", "Z", "J", "U", "O"])
def test_valid_uniprot_ambiguity_and_special_residues_preserved(tmp_path, residue):
    sequence = f"AC{residue}E"
    paths = _fixture(tmp_path, first_sequence=sequence.encode("ascii"))
    report = _run(paths)
    rows = _records(paths[-1] / ANNEX_NAME)
    assert rows[0]["sequence"] == sequence
    assert (
        rows[0]["sequence_sha256"]
        == hashlib.sha256(sequence.encode("ascii")).hexdigest()
    )
    assert report["counts"]["recovered_unique_rids"] == 2
    assert report["go_donor_decisions"] == 0


@pytest.mark.parametrize("invalid", [b"AC*E", b"AC?E", b"ACxE", b"AC E"])
def test_non_protein_residue_characters_remain_rejected(tmp_path, invalid):
    paths = _fixture(tmp_path, first_sequence=invalid)
    with pytest.raises(ValueError, match="sequence conflict"):
        _run(paths)
    assert not paths[-1].exists()


def test_no_pending_does_not_read_large_fasta(tmp_path):
    paths = _fixture(tmp_path, empty=True)
    report = _run(paths)
    assert report["counts"]["requested_unique_rids"] == 0
    assert report["go_fasta_scanned"] is False
    assert _records(paths[-1] / ANNEX_NAME) == []


def test_refuse_overwrite(tmp_path):
    paths = _fixture(tmp_path)
    _run(paths)
    with pytest.raises(FileExistsError):
        _run(paths)


def test_tampered_coverage_checksum(tmp_path):
    paths = _fixture(tmp_path)
    with (paths[0] / PENDING_NAME).open("a") as handle:
        handle.write("\n")
    with pytest.raises(ValueError, match="checksum"):
        _run(paths)
    assert not paths[-1].exists()


@pytest.mark.parametrize(
    "field,value,expected",
    [
        ("rid", "99", "RID"),
        ("candidate_accession", "WRONG", "metadata"),
        ("raw_ox", "303", "metadata"),
        ("go_positive_record_number", "4", "Missing"),
        ("reason", "UNKNOWN", "provenance"),
    ],
)
def test_request_mismatch(tmp_path, field, value, expected):
    paths = _fixture(tmp_path)
    rows = _records(paths[0] / PENDING_NAME)
    rows[0][field] = value
    _write(paths[0] / PENDING_NAME, PENDING_FIELDS, rows)
    _refresh(paths[0])
    with pytest.raises(ValueError, match=expected):
        _run(paths)
    assert not paths[-1].exists()


@pytest.mark.parametrize("field", ["rid", "go_positive_record_number"])
def test_duplicate_request(tmp_path, field):
    paths = _fixture(tmp_path)
    rows = _records(paths[0] / PENDING_NAME)
    rows[1][field] = rows[0][field]
    _write(paths[0] / PENDING_NAME, PENDING_FIELDS, rows)
    _refresh(paths[0])
    with pytest.raises(ValueError, match="Duplicate"):
        _run(paths)


def test_fasta_size_same_tamper_rejected_by_sha(tmp_path):
    paths = _fixture(tmp_path)
    fasta = paths[1].parent / "01C_go_positive_v2.fasta"
    fasta.write_bytes(fasta.read_bytes().replace(b"ACDG", b"ACDF"))
    with pytest.raises(ValueError, match="SHA256"):
        _run(paths)
    assert not paths[-1].exists()


def test_release_mismatch_rejected(tmp_path):
    paths = _fixture(tmp_path)
    with pytest.raises(ValueError, match="coverage summary"):
        recover_pending_go(
            release="OTHER",
            coverage_dir=paths[0],
            go_manifest=paths[1],
            output_dir=paths[2],
        )


def test_stage_01c_release_mismatch_rejected(tmp_path):
    paths = _fixture(tmp_path)
    manifest = json.loads(paths[1].read_text())
    manifest["release_id"] = "OTHER"
    paths[1].write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="Wrong Stage 01C"):
        _run(paths)


def test_failure_on_frag_record(tmp_path):
    paths = _fixture(tmp_path)
    rows = _records(paths[0] / PENDING_NAME)
    rows[1]["rid"] = "9"
    rows[1]["candidate_accession"] = "A9"
    rows[1]["raw_ox"] = "303"
    rows[1]["go_positive_record_number"] = "2"
    _write(paths[0] / PENDING_NAME, PENDING_FIELDS, rows)
    _refresh(paths[0])
    with pytest.raises(ValueError, match="conflict"):
        _run(paths)


def test_invalid_directory_within_input(tmp_path):
    paths = _fixture(tmp_path)
    with pytest.raises(ValueError, match="inside"):
        recover_pending_go(
            release="R1",
            coverage_dir=paths[0],
            go_manifest=paths[1],
            output_dir=paths[0] / "annex",
        )


def test_preflight_checks_sources_but_never_scans(tmp_path):
    paths = _fixture(tmp_path)
    report = preflight_pending_go(
        release="R1", coverage_dir=paths[0], go_manifest=paths[1]
    )
    assert report["requested_unique_rids"] == 2
    assert report["pinned_fasta_size_bytes"] > 0
    assert report["go_fasta_scanned"] is False
    assert not paths[-1].exists()
