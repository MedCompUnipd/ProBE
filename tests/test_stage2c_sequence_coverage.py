"""Stage 2C sequence-readiness checks, without modifying frozen 05D."""

from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from probe.stage2c_metadata_reconciliation import (
    METADATA_OUTPUT,
    SEQUENCES_OUTPUT,
    UNIFIED_SEQUENCE_FIELDS,
)
from probe.stage2c_metadata_reconciliation import (
    SUMMARY_OUTPUT as FULL_SUMMARY,
)
from probe.stage2c_metadata_reuse import REUSED_FIELDS, STORE_FIELDS
from probe.stage2c_primary_projection import (
    ADDITIONS_NAME,
    AUDIT_NAME,
    OUTPUT_NAME,
    PROVENANCE_FIELDS,
    SUMMARY_NAME,
)
from probe.stage2c_sequence_coverage import (
    ANNEX_NAME,
    COVERAGE_NAME,
    PENDING_NAME,
    SUMMARY_NAME_OUT,
    _add_rid_lookup_index,
    build_sequence_coverage,
)


def _write(path: Path, fields, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=fields, delimiter="\t", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)


def _read(path: Path):
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _sha(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _row(target: str, rid: str, *, go: bool, full: bool, exact: bool):
    return {
        "release": "R1",
        "target_id": target,
        "rid": rid,
        "candidate_primary_accession": f"A{rid}",
        "entry": f"ENTRY{rid}",
        "raw_ox": "202",
        "frag": "0",
        "sequence_length": "4",
        "pe": "2",
        "sv": "1",
        "sec": "",
        "gene": "gene",
        "sequence_discovered": "1",
        "go_positive_sequence_discovered": str(int(go)),
        "sequence_search_record_number": rid if go else "",
        "full_v2_record_number": rid if full else "",
        "seq_exact_sequence": str(int(exact)),
    }


def _meta(rid: str):
    seq = "ACDE"
    return {
        **dict.fromkeys(REUSED_FIELDS, ""),
        "release": "R1",
        "rid": rid,
        "candidate_accession": f"A{rid}",
        "candidate_raw_ox": "202",
        "entry": f"ENTRY{rid}",
        "frag": "0",
        "sequence_length": "4",
        "pe": "2",
        "sv": "1",
        "sec": "",
        "gene": "gene",
        "sequence_sha256": hashlib.sha256(seq.encode()).hexdigest(),
        "source_full_v2_record_number": rid,
        "taxonomy_state": "REQUIRES_02B_REVALIDATION",
        "source_state": "PINNED_FULL_V2_METADATA_RECOVERED",
    }


def _fixture(tmp: Path):
    projected = tmp / "projection"
    projected.mkdir()
    full_dir = tmp / "full"
    full_dir.mkdir()
    canonical_dir = tmp / "canonical"
    canonical_dir.mkdir()
    store = canonical_dir / "05D.tsv"
    targets = tmp / "targets.tsv"
    dest = tmp / "out"
    rows = [
        _row("T1", "10", go=True, full=False, exact=True),
        _row("T2", "11", go=False, full=True, exact=True),
        _row("T3", "12", go=True, full=False, exact=False),
        _row("T4", "13", go=True, full=True, exact=True),
        _row("T5", "13", go=True, full=False, exact=False),
    ]
    fields = tuple(rows[0])
    _write(projected / OUTPUT_NAME, fields, rows)
    _write(projected / ADDITIONS_NAME, fields, rows)
    audit = []
    for row in rows:
        record = dict.fromkeys(PROVENANCE_FIELDS, "")
        record.update(
            {
                "release": "R1",
                "target_id": row["target_id"],
                "rid": row["rid"],
                "go_positive_species_wide": row["go_positive_sequence_discovered"],
                "full_exact_species_wide": str(int(bool(row["full_v2_record_number"]))),
                "full_sequence_verified": str(int(bool(row["full_v2_record_number"]))),
            }
        )
        audit.append(record)
    _write(projected / AUDIT_NAME, PROVENANCE_FIELDS, audit)
    (projected / SUMMARY_NAME).write_text(
        json.dumps(
            {
                "schema": "stage2c-primary-projection-v1",
                "release": "R1",
                "go_donor_decisions": 0,
                "production_05a2_replacement": False,
                "stage_05d_and_06a_ready": False,
                "output_sha256": {
                    name: _sha(projected / name)
                    for name in (OUTPUT_NAME, ADDITIONS_NAME, AUDIT_NAME)
                },
            }
        )
    )
    canon = dict.fromkeys(STORE_FIELDS, "")
    canon.update(
        {
            "release": "R1",
            "full_v2_record_number": "10",
            "rid": "10",
            "primary_accession": "A10",
            "entry": "ENTRY10",
            "raw_ox": "202",
            "frag": "0",
            "sequence_length": "4",
            "pe": "2",
            "sv": "1",
            "sec": "",
            "gene": "gene",
            "sequence": "ACDE",
            "needed_by_primary": "1",
            "needed_by_taxonomy_audit": "0",
            "primary_reference_count": "1",
            "audit_reference_count": "0",
        }
    )
    _write(store, STORE_FIELDS, [canon])
    _write(
        targets,
        ("target_id", "normalized_sequence", "sequence_length"),
        [
            {
                "target_id": f"T{i}",
                "normalized_sequence": ("ACDF" if i in (3, 5) else "ACDE"),
                "sequence_length": "4",
            }
            for i in range(1, 6)
        ],
    )
    metadata = [_meta("11"), _meta("13")]
    sequences = [
        {
            "release": "R1",
            "rid": rid,
            "candidate_accession": f"A{rid}",
            "full_v2_record_number": rid,
            "sequence": "ACDE",
            "sequence_sha256": hashlib.sha256(b"ACDE").hexdigest(),
            "source_state": "PINNED_FULL_V2_METADATA_RECOVERED",
        }
        for rid in ("11", "13")
    ]
    _write(full_dir / METADATA_OUTPUT, REUSED_FIELDS, metadata)
    _write(full_dir / SEQUENCES_OUTPUT, UNIFIED_SEQUENCE_FIELDS, sequences)
    (full_dir / FULL_SUMMARY).write_text(
        json.dumps(
            {
                "schema": "stage2c-full-metadata-reconciliation-v1",
                "release": "R1",
                "go_donor_decisions": 0,
                "taxonomy_verified": False,
                "complete_stage05a2_replacement": False,
                "output_sha256": {
                    name: _sha(full_dir / name)
                    for name in (METADATA_OUTPUT, SEQUENCES_OUTPUT)
                },
            }
        )
    )
    return projected, store, targets, full_dir, dest


def _run(paths):
    projection, store, targets, full, dest = paths
    return build_sequence_coverage(
        release="R1",
        projection_dir=projection,
        store_05d=store,
        prepared_targets=targets,
        full_metadata_dir=full,
        output_dir=dest,
    )


def _rewrite_projection_summary(projected: Path):
    file = projected / SUMMARY_NAME
    summary = json.loads(file.read_text())
    summary["output_sha256"] = {
        name: _sha(projected / name)
        for name in (OUTPUT_NAME, ADDITIONS_NAME, AUDIT_NAME)
    }
    file.write_text(json.dumps(summary))


def _rewrite_full_summary(directory: Path):
    file = directory / FULL_SUMMARY
    summary = json.loads(file.read_text())
    summary["output_sha256"] = {
        name: _sha(directory / name) for name in (METADATA_OUTPUT, SEQUENCES_OUTPUT)
    }
    file.write_text(json.dumps(summary))


def test_canonical_full_and_go_pending(tmp_path: Path):
    paths = _fixture(tmp_path)
    result = _run(paths)
    coverage = _read(paths[-1] / COVERAGE_NAME)
    annex = _read(paths[-1] / ANNEX_NAME)
    pending = _read(paths[-1] / PENDING_NAME)
    assert [row["rid"] for row in coverage] == ["10", "11", "12", "13"]
    assert coverage[0]["source_state"] == "CANONICAL_05D"
    assert coverage[1]["source_state"] == "FULL_RECONCILED_NEW_RID"
    assert coverage[2]["source_state"] == "PENDING_GO_SEQUENCE"
    assert coverage[3]["new_primary_reference_count"] == "2"
    assert coverage[3]["full_exact_reference_count"] == "1"
    assert [row["rid"] for row in annex] == ["11", "13"]
    assert [row["rid"] for row in pending] == ["12"]
    assert result["counts"]["verified_target_rid_pairs"] == 4
    assert result["counts"]["pending_unique_rids"] == 1
    assert result["production_05d_replacement"] is False
    assert result["production_06a_ready"] is False
    assert result["go_donor_decisions"] == 0
    assert json.loads((paths[-1] / SUMMARY_NAME_OUT).read_text()) == result


def test_idempotency_refuses_overwrite(tmp_path: Path):
    paths = _fixture(tmp_path)
    _run(paths)
    with pytest.raises(FileExistsError):
        _run(paths)


def test_tampered_projection_checksum_rejected(tmp_path: Path):
    paths = _fixture(tmp_path)
    with (paths[0] / ADDITIONS_NAME).open("a") as handle:
        handle.write("\n")
    with pytest.raises(ValueError, match="checksum"):
        _run(paths)
    assert not paths[-1].exists()


def test_missing_target_fails_without_publication(tmp_path: Path):
    paths = _fixture(tmp_path)
    targets = _read(paths[2])[:-1]
    _write(paths[2], ("target_id", "normalized_sequence", "sequence_length"), targets)
    with pytest.raises(ValueError, match="Missing prepared target"):
        _run(paths)
    assert not paths[-1].exists()


def test_go_exact_flag_mismatch_fails(tmp_path: Path):
    paths = _fixture(tmp_path)
    for name in (OUTPUT_NAME, ADDITIONS_NAME):
        rows = _read(paths[0] / name)
        rows[0]["seq_exact_sequence"] = "0"
        _write(paths[0] / name, tuple(rows[0]), rows)
    _rewrite_projection_summary(paths[0])
    with pytest.raises(ValueError, match="literal sequence bytes"):
        _run(paths)
    assert not paths[-1].exists()


def test_full_metadata_accession_conflict(tmp_path: Path):
    paths = _fixture(tmp_path)
    data = _read(paths[3] / METADATA_OUTPUT)
    data[0]["candidate_accession"] = "WRONG"
    _write(paths[3] / METADATA_OUTPUT, REUSED_FIELDS, data)
    _rewrite_full_summary(paths[3])
    with pytest.raises(ValueError, match="FULL metadata disagrees"):
        _run(paths)
    assert not paths[-1].exists()


def test_missing_full_record_is_error(tmp_path: Path):
    paths = _fixture(tmp_path)
    metadata = _read(paths[3] / METADATA_OUTPUT)
    sequences = _read(paths[3] / SEQUENCES_OUTPUT)
    _write(paths[3] / METADATA_OUTPUT, REUSED_FIELDS, metadata[1:])
    _write(paths[3] / SEQUENCES_OUTPUT, UNIFIED_SEQUENCE_FIELDS, sequences[1:])
    _rewrite_full_summary(paths[3])
    with pytest.raises(ValueError, match="FULL record missing"):
        _run(paths)
    assert not paths[-1].exists()


def test_duplicate_canonical_store_rejected(tmp_path: Path):
    paths = _fixture(tmp_path)
    rows = _read(paths[1])
    _write(paths[1], STORE_FIELDS, rows * 2)
    with pytest.raises(ValueError, match="Duplicate canonical"):
        _run(paths)
    assert not paths[-1].exists()


def test_canonical_full_conflict_fails(tmp_path: Path):
    paths = _fixture(tmp_path)
    # Make RID 13 already available in 05D with a physically different sequence.
    row = _read(paths[1])[0]
    other = {
        **row,
        "rid": "13",
        "full_v2_record_number": "13",
        "primary_accession": "A13",
        "entry": "ENTRY13",
        "sequence": "ACDF",
    }
    _write(paths[1], STORE_FIELDS, [row, other])
    with pytest.raises(ValueError, match="05D vs FULL sequence conflict"):
        _run(paths)
    assert not paths[-1].exists()


def test_overlapping_output_directory_rejected(tmp_path: Path):
    paths = _fixture(tmp_path)
    with pytest.raises(ValueError, match="inside an input publication"):
        build_sequence_coverage(
            release="R1",
            projection_dir=paths[0],
            store_05d=paths[1],
            prepared_targets=paths[2],
            full_metadata_dir=paths[3],
            output_dir=paths[0] / "bad_output",
        )


def test_no_full_dir_requires_no_full_references(tmp_path: Path):
    paths = _fixture(tmp_path)
    with pytest.raises(ValueError, match="Missing FULL reconciled"):
        build_sequence_coverage(
            release="R1",
            projection_dir=paths[0],
            store_05d=paths[1],
            prepared_targets=paths[2],
            output_dir=paths[-1],
        )


def test_release_mismatch_rejected(tmp_path: Path):
    paths = _fixture(tmp_path)
    with pytest.raises(ValueError, match="provenance summary"):
        build_sequence_coverage(
            release="R2",
            projection_dir=paths[0],
            store_05d=paths[1],
            prepared_targets=paths[2],
            full_metadata_dir=paths[3],
            output_dir=paths[-1],
        )


def test_duplicate_full_metadata_rejected(tmp_path: Path):
    paths = _fixture(tmp_path)
    rows = _read(paths[3] / METADATA_OUTPUT)
    _write(paths[3] / METADATA_OUTPUT, REUSED_FIELDS, rows + [rows[0]])
    _rewrite_full_summary(paths[3])
    with pytest.raises(ValueError, match="Duplicate FULL metadata"):
        _run(paths)
    assert not paths[-1].exists()


def test_rid_only_pair_lookup_uses_explicit_index():
    """RID-only scans must not walk the composite (target, rid) key."""
    db = sqlite3.connect(":memory:")
    try:
        db.execute(
            "CREATE TABLE a (target TEXT, rid INTEGER, payload TEXT NOT NULL, "
            "PRIMARY KEY(target,rid))"
        )
        db.executemany(
            "INSERT INTO a VALUES (?, ?, ?)",
            [(f"T{i}", i % 10, "{}") for i in range(1000)],
        )
        _add_rid_lookup_index(db)
        explain = db.execute(
            "EXPLAIN QUERY PLAN SELECT target, payload FROM a WHERE rid = ?",
            (5,),
        ).fetchall()
        assert any("stage2c_a_rid_idx" in row[3] for row in explain)
        rows = db.execute(
            "SELECT target, payload FROM a WHERE rid = ?", (5,)
        ).fetchall()
        assert len(rows) == 100
    finally:
        db.close()
