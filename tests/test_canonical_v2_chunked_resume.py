"""Resumable certified DP: cumulative budget is not a biological exclusion."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from test_canonical_v2_bounded_execution import _execution_inputs

from probe.canonical_v2 import bounded_dp


def _arguments(tmp_path: Path, binary: Path) -> dict:
    args = _execution_inputs(tmp_path, binary)
    workload = args["workload"]
    return {
        "binary": binary,
        "jobs": workload / "2C_06A_alignment_jobs.tsv",
        "targets": args["targets"],
        "start_store": workload / "2C_05D_START_variant.tsv",
        "end_store": workload / "2C_05D_END_variant.tsv",
        "releases": {"START": "R1", "END": "R2"},
        "plan": tmp_path / "plan.tsv",
        "max_pair_cells": 50,
        "max_total_cells": 40,
        "max_chunk_cells": 40,
    }


def test_total_exceeds_invocation_but_all_pairs_complete(
    tmp_path: Path,
    bounded_binary: Path,
) -> None:
    args = _arguments(tmp_path, bounded_binary)
    report = bounded_dp.plan_bounded_dp(**args)
    assert report["selected_pairs"] == 3
    assert report["directional_cells"] > args["max_total_cells"]
    run = dict(args)
    run.pop("max_total_cells")
    run.pop("max_chunk_cells")
    run.update(
        {
            "max_total_cells": 40,
            "max_chunk_cells": 40,
            "output": tmp_path / "output.tsv",
            "summary": tmp_path / "metrics.tsv",
            "threads": 1,
            "checkpoint_root": tmp_path / "journal",
        }
    )
    assert bounded_dp.execute_bounded_dp(**run) == 3
    assert len(bounded_dp._rows(run["output"])[1]) == 3
    assert bounded_dp._metrics(run["summary"])["completed_count"] == 3


def test_resume_reuses_completed_chunk_and_fails_closed_on_corruption(
    tmp_path: Path,
    bounded_binary: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _arguments(tmp_path, bounded_binary)
    bounded_dp.plan_bounded_dp(**args)
    run = dict(args)
    run.pop("max_total_cells")
    run.pop("max_chunk_cells")
    run.update(
        {
            "max_total_cells": 40,
            "max_chunk_cells": 40,
            "output": tmp_path / "output.tsv",
            "summary": tmp_path / "metrics.tsv",
            "threads": 1,
            "checkpoint_root": tmp_path / "journal",
        }
    )
    real_run = subprocess.run
    executions = 0

    def interrupt(command: list[str], **kwargs: object) -> object:
        nonlocal executions
        if "--output" in command:
            executions += 1
            if executions == 2:
                raise RuntimeError("Synthetic interruption between chunks")
        return real_run(command, **kwargs)

    monkeypatch.setattr(bounded_dp.subprocess, "run", interrupt)
    with pytest.raises(RuntimeError, match="Synthetic interruption"):
        bounded_dp.execute_bounded_dp(**run)
    assert not run["output"].exists()
    assert (run["checkpoint_root"] / "chunk-000000" / "manifest.json").exists()
    monkeypatch.setattr(bounded_dp.subprocess, "run", real_run)
    assert bounded_dp.execute_bounded_dp(**run) == 3
    first = run["output"].read_bytes()
    run["output"].unlink()
    run["summary"].unlink()
    chunk = run["checkpoint_root"] / "chunk-000000" / "alignments.tsv"
    with chunk.open("ab") as stream:
        stream.write(b"CORRUPTION")
    with pytest.raises(ValueError, match="checksum mismatch"):
        bounded_dp.execute_bounded_dp(**run)
    assert not run["output"].exists()
    assert first


def test_chunked_output_is_identical_to_single_invocation(
    tmp_path: Path,
    bounded_binary: Path,
) -> None:
    args = _arguments(tmp_path, bounded_binary)
    bounded_dp.plan_bounded_dp(**args)
    run = dict(args)
    run.pop("max_total_cells")
    run.pop("max_chunk_cells")
    common = dict(run, threads=1)
    bounded_dp.execute_bounded_dp(
        **common,
        max_total_cells=40,
        max_chunk_cells=40,
        output=tmp_path / "chunked.tsv",
        summary=tmp_path / "chunked-metrics.tsv",
        checkpoint_root=tmp_path / "journal-chunked",
    )
    bounded_dp.execute_bounded_dp(
        **common,
        max_total_cells=200,
        max_chunk_cells=200,
        output=tmp_path / "single.tsv",
        summary=tmp_path / "single-metrics.tsv",
        checkpoint_root=tmp_path / "journal-single",
    )
    assert (tmp_path / "chunked.tsv").read_bytes() == (
        tmp_path / "single.tsv"
    ).read_bytes()
    assert (tmp_path / "chunked-metrics.tsv").read_bytes() == (
        tmp_path / "single-metrics.tsv"
    ).read_bytes()
