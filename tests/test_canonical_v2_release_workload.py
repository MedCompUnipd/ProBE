"""Complete DP workload includes new documentary records before Stage 06A."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest
from test_canonical_v2_release_candidate_boundary import _setup
from test_canonical_v2_stage05_06a import ROOT
from test_canonical_v2_stage05_06a import _fixture as source_fixture

from probe.canonical_v2.release_workload import (
    DEFERRED,
    EVIDENCE,
    SCHEMA,
    build_release_workload,
)
from probe.stage2c_alignment_completion import _preview
from probe.stage2c_alignment_preview import (
    JOBS_OUT,
    SUMMARY_OUT,
    UNION_OUT,
)
from probe.workflow.artifacts import Artifact, StageManifest


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as source:
        return list(csv.DictReader(source, delimiter="\t"))


def _write(path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        raise ValueError("Expected populated synthetic rows")
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=list(rows[0]), delimiter="\t",
            lineterminator="\n", extrasaction="raise",
        )
        writer.writeheader()
        writer.writerows(rows)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    def extended_source(path: Path) -> dict:
        args = source_fixture(path)
        folder = args["full_manifests"]["START"].parent
        fasta = folder / "01A_full_v2.fasta"
        with fasta.open("a", encoding="ascii") as stream:
            stream.write(
                ">sp|A5|ENTRY5 RID=5 SRC=1 REC=5 LINE=11 "
                "OX=111 FRAG=0 LEN=4 PE=1 SV=2 "
                "SEC=B55 GENE=abc\nACDF\n"
            )
        manifest = args["full_manifests"]["START"]
        manifest.unlink()
        StageManifest(
            stage="01A_FULL", role="START", release_id="R1",
            parameters={}, ontology=None,
            implementation={"name": "synthetic", "version": "1"},
            inputs=(), outputs=(
                Artifact("01A_full_v2.fasta", fasta.stat().st_size, _sha(fasta)),
            ),
        ).write(manifest)
        return args

    monkeypatch.setattr(
        "test_canonical_v2_release_candidate_boundary.b3_fixture",
        extended_source,
    )
    details = _setup(tmp_path)
    evidence = details["handoff"] / "documentary_evidence_observations.tsv"
    observations = _read(evidence)
    for row in observations:
        row["candidate_species_taxid"] = "100"
    _write(evidence, observations)
    summary = details["handoff"] / "documentary_handoff.summary.json"
    payload = json.loads(summary.read_text(encoding="utf-8"))
    payload["output_sha256"][evidence.name] = _sha(evidence)
    summary.write_text(json.dumps(payload), encoding="utf-8")
    return {
        "b3": details["b3"],
        "handoff": details["handoff"],
        "releases": details["releases"],
        "targets": tmp_path / "targets.tsv",
        "full_manifests": {
            role: tmp_path / f"full_{role}" / "manifest.json"
            for role in ("START", "END")
        },
        "stage06a_cli": ROOT / "scripts/pipeline/06A_prepare_alignment_jobs.py",
        "destination": tmp_path / "complete_workload",
    }


def test_documentary_full_recovered_before_alignment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _fixture(tmp_path, monkeypatch)
    report = build_release_workload(**args)
    folder = args["destination"]
    assert report["canonical_contract"] == SCHEMA
    assert report["documentary_donor_authorized"] is False
    assert report["counts"]["START_recovered_rid"] == 1
    assert report["counts"]["START_literal_candidates"] == 1
    assert report["counts"]["total_candidate_pairs"] == 5
    jobs = _read(folder / JOBS_OUT)
    assert len(jobs) == 5
    new = next(row for row in jobs if row["rid"] == "5")
    assert new["release"] == "R1"
    assert new["exact_sequence"] == "0"
    assert new["discovery_channel"] == "CANONICAL_DOCUMENTARY_V2"
    assert len(_read(folder / UNION_OUT)) == 5
    assert len(_read(folder / EVIDENCE)) == 2
    assert len(_read(folder / DEFERRED)) == 0
    assert report["output_sha256"][JOBS_OUT] == _sha(folder / JOBS_OUT)
    assert json.loads((folder / SUMMARY_OUT).read_text()) == report
    metadata, files = _preview(folder)
    assert metadata["legacy_resolver_compatible"] is False
    assert files[JOBS_OUT] == folder / JOBS_OUT
    assert not (folder / "candidate.sqlite3").exists()


def test_wrong_documentary_full_hash_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _fixture(tmp_path, monkeypatch)
    candidate = args["handoff"] / "documentary_candidate_handoff.tsv"
    evidence = args["handoff"] / "documentary_evidence_observations.tsv"
    for path in (candidate, evidence):
        rows = _read(path)
        rows[-1]["candidate_sequence_sha256"] = "a" * 64
        _write(path, rows)
    summary = args["handoff"] / "documentary_handoff.summary.json"
    payload = json.loads(summary.read_text())
    payload["output_sha256"][candidate.name] = _sha(candidate)
    payload["output_sha256"][evidence.name] = _sha(evidence)
    summary.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="FULL identity mismatch"):
        build_release_workload(**args)
    assert not args["destination"].exists()


def test_output_path_never_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _fixture(tmp_path, monkeypatch)
    args["destination"].mkdir()
    with pytest.raises(FileExistsError, match="occupied"):
        build_release_workload(**args)


def test_documentary_normalized_hint_never_generates_dp_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _fixture(tmp_path, monkeypatch)
    from probe.canonical_v2.documentary_handoff import (
        CANDIDATES,
        SUMMARY,
    )
    from probe.canonical_v2.documentary_handoff import (
        EVIDENCE as OBSERVATIONS,
    )

    source = args["handoff"]
    candidates = _read(source / CANDIDATES)
    candidates[-1]["literal_matches"] = "0"
    candidates[-1]["normalized_hints"] = "1"
    _write(source / CANDIDATES, candidates)
    observations = _read(source / OBSERVATIONS)
    observations[-1]["match_class"] = "NORMALIZED_HINT"
    _write(source / OBSERVATIONS, observations)
    payload = json.loads((source / SUMMARY).read_text())
    payload["output_sha256"][CANDIDATES] = _sha(source / CANDIDATES)
    payload["output_sha256"][OBSERVATIONS] = _sha(source / OBSERVATIONS)
    (source / SUMMARY).write_text(json.dumps(payload))
    report = build_release_workload(**args)
    assert report["counts"]["START_deferred_hints"] == 1
    assert report["counts"]["total_candidate_pairs"] == 4
    assert len(_read(args["destination"] / JOBS_OUT)) == 4
    assert len(_read(args["destination"] / DEFERRED)) == 1
    assert len(_read(args["destination"] / EVIDENCE)) == 2


def test_b3_pins_upstream_input_sha(tmp_path: Path,
                                 monkeypatch: pytest.MonkeyPatch) -> None:
    args = _fixture(tmp_path, monkeypatch)
    args["targets"].write_bytes(args["targets"].read_bytes() + b"\n")
    with pytest.raises(ValueError, match="upstream input SHA256"):
        build_release_workload(**args)
    assert not args["destination"].exists()
