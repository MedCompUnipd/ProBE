"""Diagnostic publication tests with no dependence on the large GOA corpus."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts/pipeline/08_profile_benchmark.py"
)


def _tsv(path: Path, fields: list[str], records: list[tuple[str, ...]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(fields)
        writer.writerows(records)


@pytest.fixture
def inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    benchmark = tmp_path / "benchmark"
    benchmark.mkdir()
    _tsv(
        benchmark / "target_aspects.tsv",
        ["query_id", "aspect", "eval_terms"],
        [("Q111", "F", "2"), ("Q222", "P", "1")],
    )
    _tsv(
        benchmark / "benchmark_terms.tsv",
        ["query_id", "aspect", "go_id", "status"],
        [
            ("Q111", "F", "GO:1000001", "EVAL"),
            ("Q111", "F", "GO:1000002", "EVAL"),
            ("Q111", "F", "GO:1000003", "SKIP"),
            ("Q111", "F", "GO:1000004", "NEUTRAL"),
            ("Q222", "P", "GO:1000001", "EVAL"),
        ],
    )
    _tsv(
        benchmark / "term_audit.tsv",
        ["query_id", "aspect", "go_id", "status", "reason"],
        [
            ("Q111", "F", "GO:1000001", "EVAL", "NOVEL"),
            ("Q111", "F", "GO:1000002", "EVAL", "CONFIRMATION"),
            ("Q111", "F", "GO:1000003", "SKIP", "ALREADY_SCORE_AT_START"),
            ("Q111", "F", "GO:1000004", "NEUTRAL", "CONFLICT_SCORE_END"),
            ("Q222", "P", "GO:1000001", "EVAL", "NOVEL"),
        ],
    )
    (benchmark / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "test-v2",
                "counts": {
                    "benchmark_EVAL": 3,
                    "benchmark_SKIP": 1,
                    "benchmark_NEUTRAL": 1,
                    "eligible_target_aspects": 2,
                },
            }
        ),
        encoding="utf-8",
    )
    targets = tmp_path / "targets.tsv"
    _tsv(
        targets,
        ["target_id", "ncbi_taxid", "uniprot_accession"],
        [
            ("T1", "9606", "Q111"),
            ("T2", "9606", "Q111"),
            ("T3", "10090", "Q222"),
        ],
    )
    stage01 = tmp_path / "01_targets.tsv"
    _tsv(
        stage01,
        [
            "target_id",
            "raw_taxid",
            "optional_accession",
            "normalized_sequence",
            "sequence_length",
            "sequence_sha256",
            "normalization_policy_version",
        ],
        [
            ("T1", "9606", "Q111", "AAA", "100", "hash1", "v1"),
            ("T2", "9606", "Q111", "AAA", "100", "hash1", "v1"),
            ("T3", "10090", "Q222", "AAAA", "1300", "hash2", "v1"),
        ],
    )
    return benchmark, targets, stage01


def _run(
    inputs: tuple[Path, Path, Path],
    output: Path,
    *options: str,
) -> subprocess.CompletedProcess[str]:
    benchmark, targets, stage01 = inputs
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--benchmark",
            str(benchmark),
            "--targets",
            str(targets),
            "--stage01",
            str(stage01),
            "--output",
            str(output),
            *options,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_counts_and_immutable_publication(
    inputs: tuple[Path, Path, Path],
    tmp_path: Path,
) -> None:
    output = tmp_path / "result"
    first = _run(inputs, output)
    assert first.returncode == 0, first.stderr
    summary = json.loads((output / "summary.json").read_text())
    assert summary["eligible_distinct_proteins"] == 2
    assert summary["eligible_target_aspects"] == 2
    assert summary["associations"] == {
        "EVAL": 3,
        "SKIP": 1,
        "NEUTRAL": 1,
    }
    assert summary["total_distinct_go_across_statuses"] == 4
    assert summary["identical_target_aliases"] == 1
    assert summary["protein_lengths_aa"]["over_1022"] == 1
    assert (output / "publication_manifest.json").is_file()
    assert not any(tmp_path.glob(".result.stage-*"))
    rerun = _run(inputs, output)
    assert rerun.returncode != 0
    assert (output / "summary.json").is_file()


def test_length_filter_keeps_source_unchanged(
    inputs: tuple[Path, Path, Path],
    tmp_path: Path,
) -> None:
    output = tmp_path / "long"
    result = _run(inputs, output, "--min-length", "1023")
    assert result.returncode == 0, result.stderr
    summary = json.loads((output / "summary.json").read_text())
    assert summary["eligible_distinct_proteins"] == 1
    assert summary["eligible_target_aspects"] == 1
    assert summary["associations"]["EVAL"] == 1
    assert (inputs[0] / "manifest.json").exists()


def test_invalid_input_does_not_publish_partial_output(
    inputs: tuple[Path, Path, Path],
    tmp_path: Path,
) -> None:
    benchmark = inputs[0]
    with (benchmark / "benchmark_terms.tsv").open("a", encoding="utf-8") as handle:
        handle.write("Q111\tF\tGO:1000005\tWRONG\n")
    output = tmp_path / "invalid"
    result = _run(inputs, output)
    assert result.returncode != 0
    assert not output.exists()
    assert not any(tmp_path.glob(".invalid.stage-*"))


def test_missing_ia_remains_explicit(
    inputs: tuple[Path, Path, Path],
    tmp_path: Path,
) -> None:
    output = tmp_path / "no_ia"
    result = _run(inputs, output)
    assert result.returncode == 0, result.stderr
    summary = json.loads((output / "summary.json").read_text())
    assert summary["information_accretion"]["status"] == "not_available"
    assert not (output / "ia_bins.tsv").exists()


def test_target_aspect_count_mismatch_aborts(
    inputs: tuple[Path, Path, Path],
    tmp_path: Path,
) -> None:
    benchmark = inputs[0]
    _tsv(
        benchmark / "target_aspects.tsv",
        ["query_id", "aspect", "eval_terms"],
        [("Q111", "F", "99"), ("Q222", "P", "1")],
    )
    output = tmp_path / "bad_cardinality"
    result = _run(inputs, output)
    assert result.returncode != 0
    assert "target_aspects count mismatch" in result.stderr
    assert not output.exists()


def test_collision_with_different_taxon_rejected(
    inputs: tuple[Path, Path, Path],
    tmp_path: Path,
) -> None:
    stage01 = inputs[2]
    content = stage01.read_text(encoding="utf-8")
    stage01.write_text(
        content.replace("T2\t9606\tQ111", "T2\t10090\tQ111"),
        encoding="utf-8",
    )
    output = tmp_path / "bad_identity"
    result = _run(inputs, output)
    assert result.returncode != 0
    assert not output.exists()
