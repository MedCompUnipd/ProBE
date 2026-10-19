"""Actual native certified DP through the canonical selector and finalizer."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from test_canonical_v2_alignment_v2 import _fixture, _read, _write

from probe.canonical_v2.alignment_v2 import execute_alignment_v2
from probe.canonical_v2.bounded_dp import BACKEND, PLAN, read_plan
from probe.canonical_v2.documentary_handoff import EVIDENCE_FIELDS
from probe.canonical_v2.release_workload import EVIDENCE
from probe.stage2c_alignment_completion import FINAL_OUTPUT
from probe.stage2c_alignment_preview import (
    AUDIT_OUT,
    END_STORE,
    JOBS_OUT,
    START_STORE,
    UNION_OUT,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def bounded_binary(tmp_path_factory: pytest.TempPathFactory) -> Path:
    compiler = shutil.which("g++")
    assert compiler is not None, "The certified native gate requires g++"
    binary = tmp_path_factory.mktemp("bounded-native") / "bounded-dp"
    subprocess.run(
        [
            compiler,
            "-O2",
            "-std=c++17",
            str(ROOT / "cpp/src/probe_directional_glocal_bounded_dp_v2.cpp"),
            "-o",
            str(binary),
        ],
        check=True,
    )
    return binary


def _execution_inputs(tmp_path: Path, binary: Path) -> dict:
    source = _fixture(tmp_path)
    workload = tmp_path / "workload"
    workload.mkdir()
    for name, key in (
        (UNION_OUT, "primary"),
        (AUDIT_OUT, "audit"),
    ):
        shutil.copyfile(source[key], workload / name)
    jobs = _read(source["jobs"])
    for row in jobs:
        row["target_length"] = "5"
        row["target_optional_accession"] = "X1"
    _write(workload / JOBS_OUT, tuple(jobs[0]), jobs)
    fields = ("release", "rid", "primary_accession", "sequence")
    _write(
        workload / START_STORE,
        fields,
        [
            {
                "release": "R1",
                "rid": "1",
                "primary_accession": "P1",
                "sequence": "AAAAB",
            },
            {
                "release": "R1",
                "rid": "2",
                "primary_accession": "P2",
                "sequence": "AAAAC",
            },
        ],
    )
    _write(
        workload / END_STORE,
        fields,
        [
            {
                "release": "R2",
                "rid": "1",
                "primary_accession": "Q1",
                "sequence": "AAAAD",
            },
        ],
    )
    evidence = _read(source["documentary_evidence"])
    evidence[0]["candidate_sequence_sha256"] = hashlib.sha256(b"AAAAB").hexdigest()
    _write(workload / EVIDENCE, EVIDENCE_FIELDS, evidence)
    names = (JOBS_OUT, UNION_OUT, AUDIT_OUT, START_STORE, END_STORE, EVIDENCE)
    hashes = {
        name: hashlib.sha256((workload / name).read_bytes()).hexdigest()
        for name in names
    }
    (workload / "2C_alignment_preview.summary.json").write_text(
        json.dumps(
            {
                "schema": "stage2c-alignment-preview-v1",
                "canonical_contract": "probe-canonical-v2-complete-workload-v1",
                "releases": source["releases"],
                "alignment_executed": False,
                "donor_decisions": 0,
                "production_stages_replaced": False,
                "output_sha256": hashes,
            }
        )
    )
    targets = tmp_path / "targets.tsv"
    _write(
        targets,
        ("target_id", "optional_accession", "normalized_sequence"),
        [
            {
                "target_id": "T",
                "optional_accession": "X1",
                "normalized_sequence": "AAAAA",
            },
        ],
    )
    return {
        "workload": workload,
        "targets": targets,
        "taxonomy": source["taxonomy"],
        "lineage_index": source["lineage_index"],
        "lineage_paths": source["lineage_paths"],
        "nonexact_script": (
            ROOT / "scripts/pipeline/NONEXACT_select_alignment_jobs.py"
        ),
        "full_dp_binary": binary,
        "finalize_script": (
            ROOT / "scripts/pipeline/06C1_finalize_directional_glocal.py"
        ),
        "destination": tmp_path / "aligned",
        "dp_backend": BACKEND,
        "threads": 1,
        "max_pair_cells": 50,
        "max_total_cells": 50,
        "max_chunk_cells": 50,
    }


def test_certified_execution_selects_only_documented_same_taxid_pair(
    tmp_path: Path,
    bounded_binary: Path,
) -> None:
    arguments = _execution_inputs(tmp_path, bounded_binary)
    report = execute_alignment_v2(**arguments)
    assert report["dp_gate"] == {"nonexact": 3, "selected": 1, "skipped": 2}
    assert report["dp_backend"] == BACKEND
    assert read_plan(arguments["destination"] / PLAN) == {
        ("R1", "T", "1"): (16, 16),
    }
    alignments = _read(arguments["destination"] / FINAL_OUTPUT)
    assert len(alignments) == 1
    row = alignments[0]
    assert row["rid"] == "1" and row["release"] == "R1"
    for direction in ("target_global", "candidate_global"):
        assert row[f"{direction}_matches"] == "4"
        assert row[f"{direction}_mismatches"] == "1"
        assert row[f"{direction}_cells_evaluated"] == "16"


def test_resource_failure_never_publishes_a_partial_alignment_bundle(
    tmp_path: Path,
    bounded_binary: Path,
) -> None:
    arguments = _execution_inputs(tmp_path, bounded_binary)
    arguments["max_total_cells"] = 31
    with pytest.raises(ValueError, match="INCOMPLETE_BUDGET"):
        execute_alignment_v2(**arguments)
    assert not arguments["destination"].exists()
    reports = list(
        tmp_path.glob("aligned.dp-budget-failure-*/canonical_dp_preflight_failure.json")
    )
    assert len(reports) == 1
    report = json.loads(reports[0].read_text())
    assert report["status"] == "INCOMPLETE_BUDGET"
    assert report["dp_executed"] is False
    assert report["scientific_output_published"] is False
    assert read_plan(reports[0].parent / PLAN) == {
        ("R1", "T", "1"): (16, 16),
    }
