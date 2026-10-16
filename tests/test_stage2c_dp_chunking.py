"""Chunked 06C0 must preserve the 06C1 scientific table and guards."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

import probe.stage2c_alignment_completion as completion
from probe.stage2c_alignment_preview import (
    AUDIT_OUT,
    END_STORE,
    JOBS_OUT,
    SUMMARY_OUT,
    UNION_OUT,
)


def _tsv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def _sample(tmp_path: Path) -> dict:
    root = tmp_path / "inputs"
    preview = root / "preview"
    preview.mkdir(parents=True)
    targets = root / "targets.tsv"
    _tsv(
        targets,
        ["target_id", "optional_accession", "normalized_sequence"],
        [["t1", "", "AAAA"], ["t2", "", "ACGT"]],
    )
    from probe.stage2c_alignment_preview import START_STORE

    stores = ["release", "rid", "primary_accession", "sequence"]
    _tsv(
        preview / START_STORE,
        stores,
        [["A", "1", "P1", "AAAA"], ["A", "2", "P2", "ACGA"]],
    )
    _tsv(
        preview / END_STORE,
        stores,
        [["Z", "1", "P1", "AAAA"], ["Z", "2", "P2", "ACGA"]],
    )
    fields = [
        "release", "target_id", "rid", "job_scope", "discovery_channel",
        "target_optional_accession", "candidate_primary_accession",
        "target_length", "candidate_length", "exact_sequence", "raw_ox",
    ]
    rows = [
        ["A", "t1", "1", "PRIMARY", "SEQUENCE", "", "P1", "4", "4", "1", "11"],
        ["A", "t2", "2", "PRIMARY", "SEQUENCE", "", "P2", "4", "4", "0", "11"],
        ["Z", "t1", "1", "PRIMARY", "SEQUENCE", "", "P1", "4", "4", "1", "11"],
        ["Z", "t2", "2", "PRIMARY", "SEQUENCE", "", "P2", "4", "4", "0", "11"],
    ]
    _tsv(preview / JOBS_OUT, fields, rows)
    work_root = root / "work"
    canonical_primary = root / "canonical_05a2.tsv"
    tax_fields = [
        "target_id", "raw_taxid", "resolution_status", "resolved_taxid",
        "resolved_rank", "species_taxid", "primary_bucket",
    ]
    primary_fields = [
        "release", "target_id", "rid", "candidate_primary_accession",
        "raw_ox", "resolved_taxid", "species_taxid", "sequence_length",
        "discovery_channel",
    ]
    primary = [
        ["A", "t1", "1", "P1", "11", "11", "11", "4", "SEQUENCE"],
        ["A", "t2", "2", "P2", "11", "11", "11", "4", "SEQUENCE"],
        ["Z", "t1", "1", "P1", "11", "11", "11", "4", "SEQUENCE"],
        ["Z", "t2", "2", "P2", "11", "11", "11", "4", "SEQUENCE"],
    ]
    audit_fields = [
        "release", "target_id", "rid", "candidate_primary_accession",
        "raw_ox", "target_resolved_taxid", "anchor_resolved_taxid",
        "anchor_species_taxid", "audit_alignment_allowed",
        "key_source_reason", "audit_class",
        "record_already_in_primary_union_for_target",
    ]
    for role in ("START", "END"):
        path = work_root / role / "02B" / "02B_target_taxonomy.tsv"
        path.parent.mkdir(parents=True)
        _tsv(
            path, tax_fields,
            [["t1", "11", "CURRENT", "11", "species", "11", "T:11"],
             ["t2", "11", "CURRENT", "11", "species", "11", "T:11"]],
        )
    _tsv(preview / UNION_OUT, primary_fields, primary)
    _tsv(preview / AUDIT_OUT, audit_fields, [])
    _tsv(canonical_primary, primary_fields, primary)
    (preview / SUMMARY_OUT).write_text(
        json.dumps(
            {
                "schema": "stage2c-alignment-preview-v1",
                "releases": {"START": "A", "END": "Z"},
                "alignment_executed": False,
                "donor_decisions": 0,
                "production_stages_replaced": False,
                "input_sha256": {
                    str(canonical_primary): completion.sha256(canonical_primary)
                },
                "output_sha256": {
                    name: completion.sha256(preview / name)
                    for name in (JOBS_OUT, START_STORE, END_STORE, UNION_OUT, AUDIT_OUT)
                },
            }
        ),
        encoding="utf-8",
    )
    repo = Path(__file__).resolve().parents[1]
    binary = root / "fake-dp-executable"
    binary.write_text("placeholder", encoding="utf-8")
    binary.chmod(0o700)
    return {
        "preview": preview,
        "targets": targets,
        "work_root": work_root,
        "canonical_primary": canonical_primary,
        "nonexact_script": (
            repo / "scripts/pipeline/NONEXACT_select_alignment_jobs.py"
        ),
        "full_dp_binary": binary,
        "finalize_script": (
            repo / "scripts/pipeline/06C1_finalize_directional_glocal.py"
        ),
        "threads": 1,
        "max_pair_cells": 50,
        "max_total_cells": 100,
    }


def _fake_dp(command: list[str]) -> None:
    def option(name: str) -> Path:
        return Path(command[command.index(name) + 1])

    with option("--alignment-jobs").open(newline="", encoding="utf-8") as fh:
        jobs = list(csv.DictReader(fh, delimiter="\t"))
    header = completion._dp_header()
    with option("--output").open("x", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=header, delimiter="\t", lineterminator="\n"
        )
        writer.writeheader()
        for job in jobs:
            row = {name: job[name] for name in header[:9]}
            for side in ("target_global", "candidate_global"):
                data = {
                    "status": "OK",
                    "score": "5",
                    "target_start0": "0",
                    "target_end0_exclusive": "4",
                    "candidate_start0": "0",
                    "candidate_end0_exclusive": "4",
                    "target_aligned_span": "4",
                    "candidate_aligned_span": "4",
                    "target_coverage": "1.0000000000",
                    "candidate_coverage": "1.0000000000",
                    "matches": "3",
                    "mismatches": "1",
                    "gap_in_target_residues": "0",
                    "gap_in_candidate_residues": "0",
                    "gap_openings": "0",
                    "aligned_residue_pairs": "4",
                    "alignment_columns": "4",
                    "identity_aligned_pairs": "0.7500000000",
                    "identity_alignment_columns": "0.7500000000",
                    "cells_evaluated": "25",
                }
                row.update({f"{side}_{k}": v for k, v in data.items()})
            writer.writerow(row)
    metrics = {
        "job_count": len(jobs),
        "completed_count": len(jobs),
        "threads": 1,
        "directional_alignment_count": 2 * len(jobs),
        "total_dp_cells_evaluated": 50 * len(jobs),
        "status_OK": 2 * len(jobs),
    }
    _tsv(
        option("--summary"),
        ["metric", "value"],
        [[key, str(value)] for key, value in metrics.items()],
    )


def test_chunks_preserve_final_06c1(tmp_path: Path, monkeypatch) -> None:
    inputs = _sample(tmp_path)
    real_run = completion._run

    def run(command: list[str]) -> None:
        if command[0] == str(inputs["full_dp_binary"]):
            _fake_dp(command)
        else:
            real_run(command)

    monkeypatch.setattr(completion, "_run", run)
    unchunked = tmp_path / "normal"
    chunked = tmp_path / "chunks"
    first = completion.complete_alignment_preview(**inputs, output_dir=unchunked)
    second = completion.complete_alignment_preview(
        **inputs, output_dir=chunked, max_chunk_cells=50
    )
    assert "dp_chunks" not in first
    assert second["dp_chunks"] == 2
    assert first["counts"]["full_dp_directional_cells"] == 100
    assert second["counts"]["full_dp_directional_cells"] == 100
    for name in completion.OUTPUTS:
        assert completion.sha256(unchunked / name) == completion.sha256(chunked / name)
    assert not (chunked / ".dp-chunk-work").exists()


def test_chunking_preserves_aggregate_guard(tmp_path: Path) -> None:
    inputs = _sample(tmp_path)
    with pytest.raises(ValueError, match="total cell budget exceeded"):
        completion.complete_alignment_preview(
            **{**inputs, "max_total_cells": 99},
            output_dir=tmp_path / "never",
            max_chunk_cells=50,
        )
    assert not (tmp_path / "never").exists()


def test_chunking_rejects_pair_larger_than_chunk(tmp_path: Path) -> None:
    inputs = _sample(tmp_path)
    with pytest.raises(ValueError, match="pair exceeds"):
        completion.complete_alignment_preview(
            **inputs, output_dir=tmp_path / "never", max_chunk_cells=49
        )
    assert not (tmp_path / "never").exists()


def test_invalid_chunk_cells_rejected(tmp_path: Path) -> None:
    inputs = _sample(tmp_path)
    with pytest.raises(ValueError, match="budget"):
        completion.complete_alignment_preview(
            **inputs, output_dir=tmp_path / "never", max_chunk_cells=0
        )
    assert not (tmp_path / "never").exists()
