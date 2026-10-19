"""FULL materialization preserves literal IUPAC for equality verification."""

from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from test_canonical_v2_exact_documentary_registry import _fixture as exact_fixture
from test_canonical_v2_stage05_06a import _fixture as b3_fixture

from probe.canonical_v2 import release_workload, stage05_06a
from probe.canonical_v2.exact_documentary_registry import (
    OUTPUT,
    build_exact_documentary_register,
)
from probe.canonical_v2.primary_projection import OUTPUT_NAME, SUMMARY_NAME


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def _write(
    path: Path,
    rows: list[dict[str, str]],
    fields: list[str] | None = None,
) -> None:
    if fields is None:
        fields = list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=fields, delimiter="\t", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)


def _header(sequence: bytes) -> bytes:
    return (
        ">sp|P23571|3MG_RAT RID=1 OX=10116 FRAG=1 "
        f"LEN={len(sequence)} PE=2 SV=2 GENE=N:Mpg\n"
    ).encode("ascii")


def _materialize(
    directory: Path,
    sequence: bytes,
    *,
    header: bytes | None = None,
    digest: str | None = None,
    expected_entry: str = "3MG_RAT",
) -> dict[str, str]:
    fasta = directory / "full.fasta"
    fasta.write_bytes(
        (_header(sequence) if header is None else header) + sequence + b"\n"
    )
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute(
            "CREATE TABLE request (role TEXT,rid INTEGER,expected TEXT,"
            "primary_refs INTEGER,audit_refs INTEGER,full TEXT,"
            "PRIMARY KEY(role,rid))"
        )
        stage05_06a._request(
            db,
            "START",
            {
                "rid": "1",
                "candidate_primary_accession": "P23571",
                "raw_ox": "10116",
                "frag": "1",
                "entry": expected_entry,
                "sequence_length": str(len(sequence)),
                "pe": "2",
                "sv": "2",
                "sec": "",
                "gene": "N:Mpg",
            },
            primary=True,
        )
        if digest is None:
            digest = hashlib.sha256(fasta.read_bytes()).hexdigest()
        assert stage05_06a._scan_full(db, "START", fasta, digest) == (1, 1)
        store = directory / "store.tsv"
        assert stage05_06a._write_store(db, "START", store, "2023_01") == 1
    return _rows(store)[0]


@pytest.mark.parametrize("symbol", [b"", b"B", b"J", b"X", b"Z", b"U", b"O"])
def test_full_consumers_preserve_literal_residues(
    tmp_path: Path,
    symbol: bytes,
) -> None:
    sequence = b"AC" + symbol + b"DE"
    stored = _materialize(tmp_path, sequence)
    recovered = release_workload._full_row(_header(sequence), sequence + b"\n", 1)
    for row in (stored, recovered):
        assert row["sequence"].encode("ascii") == sequence
        assert hashlib.sha256(row["sequence"].encode("ascii")).digest() == (
            hashlib.sha256(sequence).digest()
        )
        assert row["primary_accession"] == "P23571"
        assert row["entry"] == "3MG_RAT"
        assert row["rid"] == "1"
        assert row["raw_ox"] == "10116"
        assert row["frag"] == "1"
        assert row["sequence_length"] == str(len(sequence))
        assert row["gene"] == "N:Mpg"
    assert stored["release"] == "2023_01"
    assert stored["needed_by_primary"] == "1"
    assert stored["primary_reference_count"] == "1"


@pytest.mark.parametrize(
    "sequence",
    [b"", b"AC*DE", b"AC?DE", b"AC-DE", b"ACxDE", b"AC DE", b"AC\tDE", b"AC\xffDE"],
)
def test_full_consumers_still_reject_invalid_bytes(
    tmp_path: Path,
    sequence: bytes,
) -> None:
    with pytest.raises(ValueError, match="FULL.*(alphabet|sequence)"):
        _materialize(tmp_path, sequence)
    with pytest.raises(ValueError, match="Invalid FULL FASTA record"):
        release_workload._full_row(_header(sequence), sequence + b"\n", 1)


@pytest.mark.parametrize("fault", ["digest", "length", "identity", "rid"])
def test_ambiguity_preserves_b3_integrity_checks(tmp_path: Path, fault: str) -> None:
    sequence = b"ACXDE"
    arguments = {}
    if fault == "digest":
        arguments["digest"] = "0" * 64
        message = "digest or request completeness"
    elif fault == "length":
        arguments["header"] = _header(sequence).replace(b"LEN=5", b"LEN=6")
        message = "length mismatch"
    elif fault == "identity":
        arguments["expected_entry"] = "OTHER_RAT"
        message = "identity disagreement"
    else:
        arguments["header"] = _header(sequence).replace(b"RID=1", b"RID=2")
        message = "ordinal"
    with pytest.raises(ValueError, match=message):
        _materialize(tmp_path, sequence, **arguments)


@pytest.mark.parametrize("substituted_digest", [False, True])
def test_documentary_recovery_checks_literal_sequence_hash(
    substituted_digest: bool,
) -> None:
    sequence = b"ACXDE"
    row = release_workload._full_row(_header(sequence), sequence + b"\n", 1)
    digest = hashlib.sha256(b"ACDDE" if substituted_digest else sequence).hexdigest()
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute(
            "CREATE TABLE requests (role TEXT,rid INTEGER,identity TEXT,full TEXT)"
        )
        db.execute(
            "INSERT INTO requests VALUES ('START',1,?,NULL)",
            (json.dumps(["P23571", "10116", "10116", "1", digest]),),
        )
        if substituted_digest:
            with pytest.raises(ValueError, match="Documentary FULL identity mismatch"):
                release_workload._accept_full(db, "START", 1, row)
            assert db.execute("SELECT full FROM requests").fetchone()[0] is None
        else:
            release_workload._accept_full(db, "START", 1, row)
            full = json.loads(db.execute("SELECT full FROM requests").fetchone()[0])
            assert full["sequence"] == "ACXDE"


def _repin_fixture(manifest: Path) -> None:
    fasta = manifest.parent / "01A_full_v2.fasta"
    value = json.loads(manifest.read_text(encoding="utf-8"))
    value["outputs"][0]["sha256"] = hashlib.sha256(fasta.read_bytes()).hexdigest()
    value["outputs"][0]["size_bytes"] = fasta.stat().st_size
    manifest.write_text(json.dumps(value) + "\n", encoding="utf-8")


@pytest.mark.parametrize("literal_equal", [False, True])
def test_ambiguous_lineage_candidate_reaches_native_06a(
    tmp_path: Path,
    literal_equal: bool,
) -> None:
    args = b3_fixture(tmp_path)
    # Isolate the lineage observation before choosing the query sequence.
    for role, projection in args["projections"].items():
        facts = projection / OUTPUT_NAME
        records = _rows(facts)
        selected = [row for row in records if row["rid"] == "2"]
        assert len(selected) == (1 if role == "START" else 0)
        _write(facts, selected, fields=list(records[0]))
        summary_path = projection / SUMMARY_NAME
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["counts"]["unique_pairs"] = len(selected)
        summary["output_sha256"][OUTPUT_NAME] = hashlib.sha256(
            facts.read_bytes()
        ).hexdigest()
        summary_path.write_text(json.dumps(summary) + "\n", encoding="utf-8")
    if literal_equal:
        targets = _rows(args["targets"])
        targets[0]["normalized_sequence"] = "ACXF"
        _write(args["targets"], targets)
    manifest = args["full_manifests"]["START"]
    fasta = manifest.parent / "01A_full_v2.fasta"
    fasta.write_bytes(fasta.read_bytes().replace(b"ACDF\n", b"ACXF\n", 1))
    _repin_fixture(manifest)
    summary = stage05_06a.build_stage05_06a(**args)
    output = args["output_dir"]
    candidate = next(
        row for row in _rows(output / stage05_06a.STORES["START"]) if row["rid"] == "2"
    )
    assert candidate["sequence"] == "ACXF"
    job = next(
        row
        for row in _rows(output / stage05_06a.JOBS)
        if row["release"] == "R1" and row["rid"] == "2"
    )
    assert job["discovery_channel"] == "LINEAGE_ONLY"
    assert job["exact_sequence"] == str(int(literal_equal))
    assert summary["dp_executions"] == 0
    assert summary["go_donor_decisions"] == 0


@pytest.mark.parametrize("symbol", "BJXZ")
def test_literal_ambiguous_equality_is_verified_by_exact_registry(
    tmp_path: Path,
    symbol: str,
) -> None:
    paths = exact_fixture(tmp_path)
    sequence = f"A{symbol}DE"
    targets = _rows(paths["targets"])
    targets[0]["normalized_sequence"] = sequence
    _write(paths["targets"], targets)
    discoveries = _rows(paths["discovery"])
    discoveries[1]["full_exact_species_wide"] = "0"
    _write(paths["discovery"], discoveries)
    manifest = paths["full_manifest"]
    fasta = manifest.parent / "01A_full_v2.fasta"
    fasta.write_bytes(
        fasta.read_bytes().replace(b"ACDE\n", sequence.encode("ascii") + b"\n", 1)
    )
    _repin_fixture(manifest)
    summary = build_exact_documentary_register(release="R1", **paths)
    assert summary["counts"]["literal_verified_ambiguous_exact"] == 1
    assert summary["counts"]["literal_verified_exact"] == 1
    keys = _rows(paths["output_dir"] / OUTPUT)
    assert {row["exact_candidate_rid"] for row in keys} == {"1"}
    assert {row["source_full_sequence_sha256"] for row in keys} == {
        hashlib.sha256(sequence.encode("ascii")).hexdigest()
    }
    assert summary["go_donor_decisions"] == 0
    assert summary["dp_jobs_authorized"] == 0
