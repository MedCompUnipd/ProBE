"""Stage 2C4D2: reuse real production NONEXACT/06C0/06C1 contracts."""

from __future__ import annotations

import csv
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from probe.stage2c_alignment_completion import (
    FINAL_OUTPUT,
    FINAL_SUMMARY,
    OUTPUTS,
    SUMMARY,
    _dp_budget,
    complete_alignment_preview,
    sha256,
)
from probe.stage2c_alignment_preview import (
    AUDIT_OUT,
    END_STORE,
    JOBS_OUT,
    START_STORE,
    SUMMARY_OUT,
    UNION_OUT,
)

ROOT = Path(__file__).resolve().parents[1]
NONEXACT_SCRIPT = ROOT / "scripts/pipeline/NONEXACT_select_alignment_jobs.py"
FINALIZER = ROOT / "scripts/pipeline/06C1_finalize_directional_glocal.py"
CPP = ROOT / "cpp/src/probe_directional_glocal_full_dp_v1.cpp"


def tsv(path: Path, fields: list[str], records: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(fields)
        writer.writerows(records)


@pytest.fixture
def example(tmp_path: Path) -> dict:
    root = tmp_path / "inputs"
    root.mkdir()
    preview = root / "preview"
    preview.mkdir()
    target = root / "targets.tsv"
    tsv(
        target,
        ["target_id", "optional_accession", "normalized_sequence"],
        [["t1", "", "AAAA"], ["t2", "", "ACGT"]],
    )
    store_columns = ["release", "rid", "primary_accession", "sequence"]
    tsv(
        preview / START_STORE,
        store_columns,
        [["A", "1", "P1", "AAAA"], ["A", "2", "P2", "ACGA"]],
    )
    tsv(
        preview / END_STORE,
        store_columns,
        [["Z", "1", "P1", "AAAA"]],
    )
    fields = [
        "release",
        "target_id",
        "rid",
        "job_scope",
        "discovery_channel",
        "target_optional_accession",
        "candidate_primary_accession",
        "target_length",
        "candidate_length",
        "exact_sequence",
        "raw_ox",
    ]
    jobs = [
        ["A", "t1", "1", "PRIMARY", "SEQUENCE", "", "P1", "4", "4", "1", "11"],
        ["A", "t2", "2", "PRIMARY", "SEQUENCE", "", "P2", "4", "4", "0", "11"],
        ["Z", "t1", "1", "PRIMARY", "SEQUENCE", "", "P1", "4", "4", "1", "11"],
    ]
    tsv(preview / JOBS_OUT, fields, jobs)
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
        tsv(
            path, tax_fields,
            [["t1", "11", "CURRENT", "11", "species", "11", "T:11"],
             ["t2", "11", "CURRENT", "11", "species", "11", "T:11"]],
        )
    tsv(preview / UNION_OUT, primary_fields, primary)
    tsv(preview / AUDIT_OUT, audit_fields, [])
    tsv(canonical_primary, primary_fields, primary)
    metadata = {
        "schema": "stage2c-alignment-preview-v1",
        "releases": {"START": "A", "END": "Z"},
        "alignment_executed": False,
        "donor_decisions": 0,
        "production_stages_replaced": False,
        "input_sha256": {str(canonical_primary): sha256(canonical_primary)},
        "output_sha256": {
            name: sha256(preview / name)
            for name in (JOBS_OUT, START_STORE, END_STORE, UNION_OUT, AUDIT_OUT)
        },
    }
    (preview / SUMMARY_OUT).write_text(json.dumps(metadata), encoding="utf-8")
    return {
        "preview": preview,
        "targets": target,
        "work_root": work_root,
        "canonical_primary": canonical_primary,
        "nonexact_script": NONEXACT_SCRIPT,
        "finalize_script": FINALIZER,
        "output_dir": tmp_path / "completed",
        "jobs": jobs,
        "fields": fields,
    }


@pytest.fixture(scope="session")
def cpp_binary(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if shutil.which("g++") is None:
        pytest.skip("Optional local C++ compiler unavailable")
    destination = tmp_path_factory.mktemp("stage2c-full-dp") / "full-dp"
    subprocess.run(
        ["g++", "-std=c++17", "-O2", str(CPP), "-o", str(destination)],
        check=True,
        capture_output=True,
        text=True,
    )
    return destination


def options(example: dict, cpp_binary: Path) -> dict:
    return {
        **{k: v for k, v in example.items() if k not in {"jobs", "fields"}},
        "full_dp_binary": cpp_binary,
    }


def test_exact_nonexact_full_dp_and_c1(example: dict, cpp_binary: Path) -> None:
    inputs = options(example, cpp_binary)
    summary = complete_alignment_preview(**inputs)
    published = example["output_dir"]
    assert summary["counts"] == {
        "alignment_jobs": 3,
        "exact_pairs": 2,
        "nonexact_pairs": 1,
        "dp_required_nonexact_pairs": 1,
        "policy_skipped_nonexact_pairs": 0,
        "canonical_audit_policy_skipped_pairs": 0,
        "canonical_legacy_cross_taxid_policy_skipped_pairs": 0,
        "full_dp_directional_cells": 50,
        "reused_nonexact_pairs": 0,
        "computed_nonexact_pairs": 1,
        "reused_directional_cells": 0,
    }
    assert all((published / name).is_file() for name in (*OUTPUTS, SUMMARY))
    assert {name: sha256(published / name) for name in OUTPUTS} == (
        summary["output_sha256"]
    )
    with (published / FINAL_OUTPUT).open(newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    assert len(rows) == 3
    assert rows[0]["target_global_status"] == "OK_EXACT"
    assert rows[1]["target_global_status"] == "OK"
    assert rows[2]["candidate_global_status"] == "OK_EXACT"
    assert (
        json.loads((published / FINAL_SUMMARY).read_text())["canonical_pair_count"] == 3
    )
    assert example["preview"].is_dir()


def test_refuse_repeat_publication(example: dict, cpp_binary: Path) -> None:
    inputs = options(example, cpp_binary)
    complete_alignment_preview(**inputs)
    with pytest.raises(FileExistsError):
        complete_alignment_preview(**inputs)


def test_tampered_preview_hash_rejected(example: dict, cpp_binary: Path) -> None:
    with (example["preview"] / JOBS_OUT).open("a") as stream:
        stream.write("tampered\n")
    with pytest.raises(ValueError, match="SHA256"):
        complete_alignment_preview(**options(example, cpp_binary))
    assert not example["output_dir"].exists()


def test_cell_budget_guard_and_no_partial_publication(
    example: dict, cpp_binary: Path
) -> None:
    with pytest.raises(ValueError, match="budget exceeded"):
        complete_alignment_preview(**options(example, cpp_binary), max_pair_cells=49)
    assert not example["output_dir"].exists()
    assert not list(example["output_dir"].parent.glob("*.publish-lock"))


def test_nonexact_count_detects_missing_job(example: dict, cpp_binary: Path) -> None:
    tsv(example["preview"] / JOBS_OUT, example["fields"], example["jobs"][:1])
    metadata_path = example["preview"] / SUMMARY_OUT
    metadata = json.loads(metadata_path.read_text())
    metadata["output_sha256"][JOBS_OUT] = sha256(example["preview"] / JOBS_OUT)
    metadata_path.write_text(json.dumps(metadata))
    summary = complete_alignment_preview(**options(example, cpp_binary))
    assert summary["counts"]["nonexact_pairs"] == 0
    assert summary["counts"]["exact_pairs"] == 1


def test_unsorted_rows_rejected(example: dict, cpp_binary: Path) -> None:
    tsv(
        example["preview"] / JOBS_OUT,
        example["fields"],
        list(reversed(example["jobs"])),
    )
    metadata_path = example["preview"] / SUMMARY_OUT
    metadata = json.loads(metadata_path.read_text())
    metadata["output_sha256"][JOBS_OUT] = sha256(example["preview"] / JOBS_OUT)
    metadata_path.write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="Unsorted"):
        complete_alignment_preview(**options(example, cpp_binary))


@pytest.mark.parametrize("invalid", [0, -1, True])
def test_invalid_budgets(example: dict, cpp_binary: Path, invalid: int) -> None:
    with pytest.raises(ValueError, match="budget"):
        complete_alignment_preview(
            **options(example, cpp_binary), max_total_cells=invalid
        )


def test_pair_cell_budget_count(tmp_path: Path) -> None:
    path = tmp_path / "nonexact.tsv"
    tsv(path, ["target_length", "candidate_length"], [["3", "4"]])
    assert _dp_budget(path, max_pair_cells=40, max_total_cells=40) == (1, 40)
    with pytest.raises(ValueError):
        _dp_budget(path, max_pair_cells=39, max_total_cells=40)


def test_read_only_input_symlink_allowed(example: dict, cpp_binary: Path) -> None:
    alias = example["targets"].with_name("target-input-link.tsv")
    alias.symlink_to(example["targets"])
    data = options(example, cpp_binary)
    data["targets"] = alias
    result = complete_alignment_preview(**data)
    assert result["counts"]["alignment_jobs"] == 3
