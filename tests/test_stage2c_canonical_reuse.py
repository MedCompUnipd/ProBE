"""Canonical 06C1 reuse must be pinned, same-TaxID and budget-neutral."""

from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

import pytest
from test_stage2c_dp_chunking import _fake_dp, _sample, _tsv

import probe.stage2c_alignment_completion as completion
from probe.stage2c_alignment_preview import (
    END_STORE,
    JOBS_OUT,
    START_STORE,
    SUMMARY_OUT,
    UNION_OUT,
)


def _manifest(
    path: Path, stage: str, inputs: list[Path], outputs: list[Path],
    *, binary: Path | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = {
        "stage": stage,
        "status": "complete",
        "inputs": [
            {"path": str(item), "sha256": completion.sha256(item)}
            for item in inputs
        ],
        "outputs": [
            {"path": item.name, "sha256": completion.sha256(item)}
            for item in outputs
        ],
    }
    if stage == "06C0":
        metadata.update({
            "implementation": {"binary_sha256": completion.sha256(binary)},
            "parameters": {"alignment_policy": {
                "algorithm": "AFFINE_FULL_DP", "banding": False,
                "modes": ["TARGET_GLOBAL", "CANDIDATE_GLOBAL"],
                "match": 2, "mismatch": -1,
                "gap_open": -3, "gap_extend": -1,
            }},
        })
    if stage == "06C1":
        metadata["parameters"] = {
            "start_release_id": "A", "end_release_id": "Z"
        }
    path.write_text(json.dumps(metadata), encoding="utf-8")


def _history(tmp_path: Path, monkeypatch) -> tuple[dict, Path, Path]:
    inputs = _sample(tmp_path)
    work = inputs["work_root"]
    run = work.parent.parent
    history = run / "results/pipeline-v2/common/06C1"
    history.mkdir(parents=True)
    old_run = completion._run

    def mocked_run(command: list[str]) -> None:
        if command[0] == str(inputs["full_dp_binary"]):
            _fake_dp(command)
        else:
            old_run(command)

    monkeypatch.setattr(completion, "_run", mocked_run)
    first = tmp_path / "first"
    completion.complete_alignment_preview(**inputs, output_dir=first)
    shutil.copyfile(
        first / completion.FINAL_OUTPUT,
        history / "06C1_canonical_directional_glocal.tsv",
    )
    shutil.copyfile(
        first / completion.FINAL_SUMMARY, history / "06C1_summary.json"
    )
    m01 = work / "common/01/manifest.json"
    canonical_targets = m01.parent / "01_targets.tsv"
    canonical_targets.parent.mkdir(parents=True)
    shutil.copyfile(inputs["targets"], canonical_targets)
    _manifest(m01, "01", [], [canonical_targets])
    stores = {}
    preview_summary = inputs["preview"] / SUMMARY_OUT
    preview_meta = json.loads(preview_summary.read_text(encoding="utf-8"))
    for role, filename in (("START", START_STORE), ("END", END_STORE)):
        store = work / role / "05D/05D_candidate_sequences.tsv"
        store.parent.mkdir(parents=True)
        shutil.copyfile(inputs["preview"] / filename, store)
        manifest = store.parent / "manifest.json"
        _manifest(manifest, "05D", [], [store])
        preview_meta["input_sha256"][str(store)] = completion.sha256(store)
        stores[role] = manifest
    preview_summary.write_text(json.dumps(preview_meta), encoding="utf-8")
    nonexact = work / "common/NONEXACT/manifest.json"
    _manifest(nonexact, "NONEXACT", [], [])
    a = work / "common/06A/manifest.json"
    _manifest(a, "06A", [m01, *stores.values()], [])
    c = work / "common/06C0/manifest.json"
    _manifest(
        c, "06C0", [m01, nonexact, *stores.values()], [],
        binary=inputs["full_dp_binary"],
    )
    _manifest(
        history / "manifest.json", "06C1", [a, c], [
            history / "06C1_canonical_directional_glocal.tsv",
            history / "06C1_summary.json",
        ],
    )
    return inputs, history, first


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def test_reuse_all_nonexact_without_dp(tmp_path: Path, monkeypatch) -> None:
    inputs, historic, first = _history(tmp_path, monkeypatch)

    def must_not_execute_dp(command: list[str]) -> None:
        if command[0] == str(inputs["full_dp_binary"]):
            pytest.fail("A verified canonical alignment was recomputed")
        completion.subprocess.run(command, check=True, capture_output=True, text=True)

    monkeypatch.setattr(completion, "_run", must_not_execute_dp)
    output = tmp_path / "reused"
    inputs["max_pair_cells"] = 1
    inputs["max_total_cells"] = 1
    summary = completion.complete_alignment_preview(
        **inputs, output_dir=output, reuse_canonical=historic,
    )
    assert summary["counts"]["computed_nonexact_pairs"] == 0
    assert summary["counts"]["reused_nonexact_pairs"] == 2
    assert summary["counts"]["reused_directional_cells"] == 100
    assert summary["counts"]["full_dp_directional_cells"] == 0
    assert (output / completion.FINAL_OUTPUT).read_bytes() == (
        first / completion.FINAL_OUTPUT
    ).read_bytes()
    assert len(_read(output / completion.DP_OUTPUT)) == 0
    assert {p.name for p in output.iterdir()} == {
        *completion.OUTPUTS, completion.SUMMARY
    }


def test_changed_metadata_falls_back_to_dp(
    tmp_path: Path, monkeypatch
) -> None:
    inputs, historic, _ = _history(tmp_path, monkeypatch)
    jobs_path = inputs["preview"] / JOBS_OUT
    rows = _read(jobs_path)
    rows[1]["target_optional_accession"] = "NEW"
    _tsv(
        jobs_path, list(rows[0]),
        [[row[k] for k in rows[0]] for row in rows],
    )
    summary_path = inputs["preview"] / SUMMARY_OUT
    metadata = json.loads(summary_path.read_text(encoding="utf-8"))
    metadata["output_sha256"][JOBS_OUT] = completion.sha256(jobs_path)
    summary_path.write_text(json.dumps(metadata), encoding="utf-8")
    output = tmp_path / "partial"
    inputs["max_total_cells"] = 50
    completed = completion.complete_alignment_preview(
        **inputs, output_dir=output, reuse_canonical=historic,
    )
    assert completed["counts"]["computed_nonexact_pairs"] == 1
    assert completed["counts"]["reused_nonexact_pairs"] == 1
    assert completed["counts"]["full_dp_directional_cells"] == 50
    assert completed["counts"]["reused_directional_cells"] == 50
    assert len(_read(output / completion.DP_OUTPUT)) == 1
    assert len(_read(output / completion.FINAL_OUTPUT)) == 4


def test_corrupt_historic_table_fails_closed(
    tmp_path: Path, monkeypatch
) -> None:
    inputs, historic, _ = _history(tmp_path, monkeypatch)
    table = historic / "06C1_canonical_directional_glocal.tsv"
    with table.open("a", encoding="utf-8") as stream:
        stream.write("corrupted\n")
    dest = tmp_path / "refused"
    with pytest.raises(ValueError, match="Historical 06C1 SHA256 mismatch"):
        completion.complete_alignment_preview(
            **inputs, output_dir=dest, reuse_canonical=historic,
        )
    assert not dest.exists()


def test_changed_store_provenance_fails_closed(
    tmp_path: Path, monkeypatch
) -> None:
    inputs, historic, _ = _history(tmp_path, monkeypatch)
    metadata_path = inputs["preview"] / SUMMARY_OUT
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    store = inputs["work_root"] / "START/05D/05D_candidate_sequences.tsv"
    metadata["input_sha256"][str(store)] = "0" * 64
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    dest = tmp_path / "refused"
    with pytest.raises(ValueError, match="Historical/preview 05D mismatch"):
        completion.complete_alignment_preview(
            **inputs, output_dir=dest, reuse_canonical=historic,
        )
    assert not dest.exists()


def test_binary_provenance_fails_closed(tmp_path: Path, monkeypatch) -> None:
    inputs, historic, _ = _history(tmp_path, monkeypatch)
    inputs["full_dp_binary"].write_text("changed", encoding="utf-8")
    dest = tmp_path / "refused"
    with pytest.raises(ValueError, match="Historical DP implementation"):
        completion.complete_alignment_preview(
            **inputs, output_dir=dest, reuse_canonical=historic,
        )
    assert not dest.exists()


def test_cross_taxid_not_reused(tmp_path: Path, monkeypatch) -> None:
    inputs, historic, _ = _history(tmp_path, monkeypatch)
    # Historical 06C1 includes both nonexact rows. The newly verified gate
    # excludes Z/t2 even though historical DP geometry is available.
    for path in (
        inputs["canonical_primary"], inputs["preview"] / UNION_OUT
    ):
        rows = _read(path)
        for row in rows:
            if row["release"] == "Z" and row["target_id"] == "t2":
                row["resolved_taxid"] = "22"
        _tsv(path, list(rows[0]), [[r[k] for k in rows[0]] for r in rows])
    meta_path = inputs["preview"] / SUMMARY_OUT
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["input_sha256"][str(inputs["canonical_primary"])] = (
        completion.sha256(inputs["canonical_primary"])
    )
    meta["output_sha256"][UNION_OUT] = completion.sha256(
        inputs["preview"] / UNION_OUT
    )
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    inputs["max_total_cells"] = 1
    summary = completion.complete_alignment_preview(
        **inputs, output_dir=tmp_path / "mixed", reuse_canonical=historic,
    )
    assert summary["counts"]["reused_nonexact_pairs"] == 1
    assert summary["counts"]["policy_skipped_nonexact_pairs"] == 1
    assert summary["counts"]["computed_nonexact_pairs"] == 0
    assert len(_read(tmp_path / "mixed" / completion.POLICY_SKIP)) == 1


def test_unreused_same_taxid_is_budgeted_not_policy_skipped(
    tmp_path: Path, monkeypatch
) -> None:
    inputs, historic, _ = _history(tmp_path, monkeypatch)
    jobs_path = inputs["preview"] / JOBS_OUT
    rows = _read(jobs_path)
    rows[1]["target_optional_accession"] = "CHANGED"
    _tsv(jobs_path, list(rows[0]), [[r[k] for k in rows[0]] for r in rows])
    metadata_path = inputs["preview"] / SUMMARY_OUT
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["output_sha256"][JOBS_OUT] = completion.sha256(jobs_path)
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    inputs["max_total_cells"] = 1
    dest = tmp_path / "refused"
    with pytest.raises(ValueError, match="INCOMPLETE_BUDGET"):
        completion.complete_alignment_preview(
            **inputs, output_dir=dest, reuse_canonical=historic,
        )
    assert not dest.exists()
