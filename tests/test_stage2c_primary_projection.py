"""Fail-closed tests of a non-production 05A2-shaped Stage 2C projection."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from probe.stage2c_evidence_bridge import BRIDGE_FIELDS, GO_SEQ
from probe.stage2c_metadata_reconciliation import (
    METADATA_OUTPUT,
    SEQUENCES_OUTPUT,
    UNIFIED_SEQUENCE_FIELDS,
)
from probe.stage2c_metadata_reconciliation import (
    SUMMARY_OUTPUT as FULL_SUMMARY_OUTPUT,
)
from probe.stage2c_metadata_reuse import REUSED_FIELDS
from probe.stage2c_primary_projection import (
    ADDITIONS_NAME,
    AUDIT_NAME,
    OUTPUT_NAME,
    REQUIRED_BASE,
    SUMMARY_NAME,
    prepare_primary_projection,
)


def _write(path: Path, fields, rows) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=fields, delimiter="\t", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)


def _read(path: Path):
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def _bridge(target: str, rid: str, *, go: bool, full: bool):
    row = dict.fromkeys(BRIDGE_FIELDS, "")
    cross = full
    row.update(
        release="R1",
        target_id=target,
        rid=rid,
        candidate_accession=f"A{rid}",
        candidate_raw_ox="202",
        target_resolved_taxid="201",
        candidate_resolved_taxid="202" if cross else "201",
        species_taxid="200",
        go_positive_species_wide=str(int(go)),
        full_exact_species_wide=str(int(full)),
        go_positive_exact="1" if full and go else "0",
        go_positive_record_number=rid if go else "",
        full_record_number=rid if full else "",
        strict_gate="DENIED" if cross else "DEFER_TO_DOCUMENTARY",
        same_species_exact_gate="ELIGIBLE" if full else "DEFER_TO_DOCUMENTARY",
        integration_state="NEEDS_EVIDENCE_RECONSTRUCTION",
        evidence_state=(
            "GO_SCANNER_FACTS_VERIFIED" if go else "FULL_RECORD_LOOKUP_REQUIRED"
        ),
    )
    if go:
        row.update(
            target_primary_bucket="S:200",
            entry=f"ENTRY{rid}",
            sequence_length="4",
            pe="2",
            sv="1",
            sec="",
            gene="xyz",
        )
        row.update({f"seq_{name}": "1" for name in GO_SEQ})
        row["seq_target_length"] = "4"
        row["seq_candidate_length"] = "4"
        row["seq_exact_sequence"] = row["go_positive_exact"]
    return row


def _meta(rid: str):
    sequence = "ACDE"
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
        "gene": "xyz",
        "source_full_v2_record_number": rid,
        "taxonomy_state": "REQUIRES_02B_REVALIDATION",
        "source_state": "CANONICAL_05D_METADATA_REUSED",
        "sequence_sha256": hashlib.sha256(sequence.encode()).hexdigest(),
    }


def _sequence(rid: str):
    return {
        "release": "R1",
        "rid": rid,
        "candidate_accession": f"A{rid}",
        "full_v2_record_number": rid,
        "sequence": "ACDE",
        "sequence_sha256": hashlib.sha256(b"ACDE").hexdigest(),
        "source_state": "CANONICAL_05D_METADATA_REUSED",
    }


def _fixture(tmp: Path):
    canonical = tmp / "05a2.tsv"
    bridge = tmp / "bridge.tsv"
    taxonomy = tmp / "02b.tsv"
    targets = tmp / "targets.tsv"
    directory = tmp / "full"
    directory.mkdir()
    output = tmp / "projected"
    header = sorted(REQUIRED_BASE) + ["lin_source_roles"]
    canon = dict.fromkeys(header, "")
    canon.update(
        release="R1", target_id="T0", rid="5", entry="OLD", lin_source_roles="ORIGINAL"
    )
    _write(canonical, header, [canon])
    _write(
        bridge,
        BRIDGE_FIELDS,
        [
            _bridge("T1", "11", go=True, full=False),
            _bridge("T2", "12", go=False, full=True),
            _bridge("T3", "13", go=True, full=True),
        ],
    )
    _write(
        taxonomy,
        ("target_id", "resolved_taxid", "species_taxid", "primary_bucket"),
        [
            dict(
                target_id=target,
                resolved_taxid="201",
                species_taxid="200",
                primary_bucket="T:201",
            )
            for target in ("T1", "T2", "T3")
        ],
    )
    _write(
        targets,
        ("target_id", "normalized_sequence", "sequence_length"),
        [
            dict(target_id=t, normalized_sequence="ACDE", sequence_length="4")
            for t in ("T1", "T2", "T3")
        ],
    )
    _write(
        directory / METADATA_OUTPUT,
        REUSED_FIELDS,
        [
            _meta("12"),
            _meta("13"),
        ],
    )
    _write(
        directory / SEQUENCES_OUTPUT,
        UNIFIED_SEQUENCE_FIELDS,
        [
            _sequence("12"),
            _sequence("13"),
        ],
    )
    (directory / FULL_SUMMARY_OUTPUT).write_text(
        json.dumps(
            {
                "schema": "stage2c-full-metadata-reconciliation-v1",
                "release": "R1",
                "go_donor_decisions": 0,
                "taxonomy_verified": False,
                "complete_stage05a2_replacement": False,
                "output_sha256": {
                    name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
                    for name in (METADATA_OUTPUT, SEQUENCES_OUTPUT)
                },
            }
        )
    )
    return canonical, bridge, taxonomy, targets, directory, output


def _update_full_checksums(directory: Path) -> None:
    path = directory / FULL_SUMMARY_OUTPUT
    data = json.loads(path.read_text())
    for name in (METADATA_OUTPUT, SEQUENCES_OUTPUT):
        data["output_sha256"][name] = hashlib.sha256(
            (directory / name).read_bytes()
        ).hexdigest()
    path.write_text(json.dumps(data))


def _run(paths):
    canonical, bridge, taxonomy, targets, directory, output = paths
    return prepare_primary_projection(
        release="R1",
        canonical_05a2=canonical,
        bridge=bridge,
        target_taxonomy=taxonomy,
        prepared_targets=targets,
        output_dir=output,
        full_metadata_dir=directory,
    )


def test_projection_preserves_canonical_and_provenance(tmp_path: Path):
    paths = _fixture(tmp_path)
    result = _run(paths)
    rows = _read(paths[-1] / OUTPUT_NAME)
    additions = _read(paths[-1] / ADDITIONS_NAME)
    audit = _read(paths[-1] / AUDIT_NAME)
    assert len(rows) == 4
    assert rows[0]["lin_source_roles"] == "ORIGINAL"
    assert rows[0]["entry"] == "OLD"
    assert len(additions) == 3
    assert additions[0]["target_primary_bucket"] == "T:201"
    assert additions[2]["target_primary_bucket"] == "T:201"
    assert additions[0]["seq_exact_sequence"] == "0"
    assert additions[0]["lineage_discovered"] == "0"
    assert additions[1]["seq_exact_sequence"] == "1"
    assert additions[1]["seq_shared_distinct_kmers"] == ""
    assert additions[1]["sequence_search_record_number"] == ""
    assert additions[1]["full_v2_record_number"] == "12"
    assert additions[2]["seq_exact_sequence"] == "1"
    assert additions[2]["sequence_search_record_number"] == "13"
    assert additions[2]["full_v2_record_number"] == "13"
    assert audit[1]["metric_state"] == "FULL_EXACT_WITHOUT_KMER_METRICS"
    assert audit[1]["strict_gate"] == "DENIED"
    assert audit[1]["same_species_exact_gate"] == "ELIGIBLE"
    assert result["go_donor_decisions"] == 0
    assert result["production_05a2_replacement"] is False
    assert result["stage_05d_and_06a_ready"] is False
    assert json.loads((paths[-1] / SUMMARY_NAME).read_text()) == result


def test_go_species_bucket_conflict_fails_closed(tmp_path: Path):
    paths = _fixture(tmp_path)
    rows = _read(paths[1])
    rows[0]["target_primary_bucket"] = "S:201"
    _write(paths[1], BRIDGE_FIELDS, rows)
    with pytest.raises(ValueError, match="GO species-wide bucket"):
        _run(paths)
    assert not paths[-1].exists()


def test_full_sequence_mismatch_fails_closed(tmp_path: Path):
    paths = _fixture(tmp_path)
    _write(
        paths[3],
        ("target_id", "normalized_sequence", "sequence_length"),
        [
            dict(
                target_id=t,
                normalized_sequence="ACDF" if t == "T2" else "ACDE",
                sequence_length="4",
            )
            for t in ("T1", "T2", "T3")
        ],
    )
    with pytest.raises(ValueError, match="literally identical"):
        _run(paths)
    assert not paths[-1].exists()


def test_target_taxonomy_conflict_fails(tmp_path: Path):
    paths = _fixture(tmp_path)
    rows = _read(paths[2])
    rows[1]["species_taxid"] = "300"
    _write(paths[2], tuple(rows[0]), rows)
    with pytest.raises(ValueError, match="taxonomy conflicts"):
        _run(paths)
    assert not paths[-1].exists()


def test_go_and_full_metadata_conflict_fails(tmp_path: Path):
    paths = _fixture(tmp_path)
    rows = _read(paths[4] / METADATA_OUTPUT)
    rows[1]["gene"] = "different"
    _write(paths[4] / METADATA_OUTPUT, REUSED_FIELDS, rows)
    _update_full_checksums(paths[4])
    with pytest.raises(ValueError, match="metadata conflict"):
        _run(paths)
    assert not paths[-1].exists()


def test_canonical_overlap_is_rejected(tmp_path: Path):
    paths = _fixture(tmp_path)
    rows = _read(paths[0])
    rows[0]["target_id"] = "T1"
    rows[0]["rid"] = "11"
    _write(paths[0], tuple(rows[0]), rows)
    with pytest.raises(ValueError, match="overlaps canonical"):
        _run(paths)
    assert not paths[-1].exists()


def test_duplicate_bridge_is_rejected(tmp_path: Path):
    paths = _fixture(tmp_path)
    rows = _read(paths[1])
    _write(paths[1], BRIDGE_FIELDS, rows + [rows[0]])
    with pytest.raises(ValueError, match="Duplicate bridge"):
        _run(paths)
    assert not paths[-1].exists()


def test_duplicate_metadata_is_rejected(tmp_path: Path):
    paths = _fixture(tmp_path)
    rows = _read(paths[4] / METADATA_OUTPUT)
    _write(paths[4] / METADATA_OUTPUT, REUSED_FIELDS, rows + [rows[0]])
    _update_full_checksums(paths[4])
    with pytest.raises(ValueError, match="Duplicate FULL metadata"):
        _run(paths)
    assert not paths[-1].exists()


def test_missing_full_metadata_is_rejected(tmp_path: Path):
    paths = _fixture(tmp_path)
    (paths[4] / METADATA_OUTPUT).unlink()
    with pytest.raises(FileNotFoundError):
        _run(paths)
    assert not paths[-1].exists()


def test_occupied_destination_is_rejected(tmp_path: Path):
    paths = _fixture(tmp_path)
    paths[-1].mkdir()
    with pytest.raises(FileExistsError):
        _run(paths)


def test_go_source_without_full_does_not_need_metadata(tmp_path: Path):
    paths = _fixture(tmp_path)
    rows = [row for row in _read(paths[1]) if row["target_id"] == "T1"]
    _write(paths[1], BRIDGE_FIELDS, rows)
    out = prepare_primary_projection(
        release="R1",
        canonical_05a2=paths[0],
        bridge=paths[1],
        target_taxonomy=paths[2],
        prepared_targets=paths[3],
        output_dir=paths[-1],
    )
    assert out["counts"]["new_candidate_rows"] == 1


def test_full_sequence_sha_mismatch_is_rejected(tmp_path: Path):
    paths = _fixture(tmp_path)
    rows = _read(paths[4] / SEQUENCES_OUTPUT)
    rows[0]["sequence"] = "ACDF"
    _write(paths[4] / SEQUENCES_OUTPUT, UNIFIED_SEQUENCE_FIELDS, rows)
    _update_full_checksums(paths[4])
    with pytest.raises(ValueError, match="sequence identity"):
        _run(paths)
    assert not paths[-1].exists()


def test_go_exact_conflict_with_full_is_rejected(tmp_path: Path):
    paths = _fixture(tmp_path)
    rows = _read(paths[1])
    rows[2]["go_positive_exact"] = "0"
    rows[2]["seq_exact_sequence"] = "0"
    _write(paths[1], BRIDGE_FIELDS, rows)
    with pytest.raises(ValueError, match="literal sequence status"):
        _run(paths)
    assert not paths[-1].exists()


def test_full_summary_checksum_change_rejected(tmp_path: Path):
    paths = _fixture(tmp_path)
    rows = _read(paths[4] / METADATA_OUTPUT)
    rows[0]["gene"] = "changed"
    _write(paths[4] / METADATA_OUTPUT, REUSED_FIELDS, rows)
    with pytest.raises(ValueError, match="checksum mismatch"):
        _run(paths)
    assert not paths[-1].exists()


def test_bad_full_summary_schema_rejected(tmp_path: Path):
    paths = _fixture(tmp_path)
    summary = paths[4] / FULL_SUMMARY_OUTPUT
    data = json.loads(summary.read_text())
    data["schema"] = "untrusted"
    summary.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="Unverified FULL"):
        _run(paths)
    assert not paths[-1].exists()


def test_go_only_shared_rid_is_not_full_exact(tmp_path: Path):
    """Global RID availability cannot promote a GO-only target/RID pair."""
    paths = _fixture(tmp_path)
    bridge = _read(paths[1])
    shared = _bridge("T4", "12", go=True, full=False)
    shared["candidate_resolved_taxid"] = "202"
    shared["strict_gate"] = "DENIED"
    shared["same_species_exact_gate"] = "DENIED"
    bridge.append(shared)
    _write(paths[1], BRIDGE_FIELDS, bridge)
    taxonomy = _read(paths[2])
    taxonomy.append(dict(taxonomy[0], target_id="T4"))
    _write(paths[2], tuple(taxonomy[0]), taxonomy)
    targets = _read(paths[3])
    targets.append(
        dict(target_id="T4", normalized_sequence="ACDF", sequence_length="4")
    )
    _write(paths[3], tuple(targets[0]), targets)

    result = _run(paths)
    additions = _read(paths[-1] / ADDITIONS_NAME)
    audits = _read(paths[-1] / AUDIT_NAME)
    pair = next(r for r in additions if r["target_id"] == "T4")
    audit = next(r for r in audits if r["target_id"] == "T4")
    assert result["counts"]["new_candidate_rows"] == 4
    assert pair["discovery_channel"] == "STAGE2C_GO_SPECIES_WIDE"
    assert pair["seq_exact_sequence"] == "0"
    assert pair["full_v2_record_number"] == ""
    assert pair["sequence_search_record_number"] == "12"
    assert audit["full_sequence_verified"] == "0"
    assert audit["full_metadata_source"] == "GO_SCANNER"
    assert audit["same_species_exact_gate"] == "DENIED"
    assert result["go_donor_decisions"] == 0


def test_shared_go_only_rid_global_identity_mismatch_fails(tmp_path: Path):
    """A globally shared RID must not identify two different accessions."""
    paths = _fixture(tmp_path)
    bridge = _read(paths[1])
    shared = _bridge("T4", "12", go=True, full=False)
    shared["candidate_accession"] = "WRONG_ACCESSION"
    shared["candidate_resolved_taxid"] = "202"
    shared["strict_gate"] = "DENIED"
    shared["same_species_exact_gate"] = "DENIED"
    bridge.append(shared)
    _write(paths[1], BRIDGE_FIELDS, bridge)
    taxonomy = _read(paths[2])
    taxonomy.append(dict(taxonomy[0], target_id="T4"))
    _write(paths[2], tuple(taxonomy[0]), taxonomy)
    targets = _read(paths[3])
    targets.append(
        dict(target_id="T4", normalized_sequence="ACDF", sequence_length="4")
    )
    _write(paths[3], tuple(targets[0]), targets)

    with pytest.raises(ValueError, match="global FULL identity"):
        _run(paths)
    assert not paths[-1].exists()
