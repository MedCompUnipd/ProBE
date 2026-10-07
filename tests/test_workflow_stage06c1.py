import csv
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAKEFILE = ROOT / "workflow/Snakefile"
SCRIPT = ROOT / "scripts" / "pipeline" / "06C1_finalize_directional_glocal.py"
RUNNER = ROOT / "workflow/scripts/run_stage_06C1.py"

BASE_FIELDS = [
    "release",
    "target_id",
    "rid",
    "job_scope",
    "discovery_channel",
    "target_optional_accession",
    "candidate_primary_accession",
    "target_length",
    "candidate_length",
]

DIR_SUFFIXES = [
    "status",
    "score",
    "target_start0",
    "target_end0_exclusive",
    "candidate_start0",
    "candidate_end0_exclusive",
    "target_aligned_span",
    "candidate_aligned_span",
    "target_coverage",
    "candidate_coverage",
    "matches",
    "mismatches",
    "gap_in_target_residues",
    "gap_in_candidate_residues",
    "gap_openings",
    "aligned_residue_pairs",
    "alignment_columns",
    "identity_aligned_pairs",
    "identity_alignment_columns",
    "cells_evaluated",
]

EXPECTED_FIELDS = (
    BASE_FIELDS
    + [f"target_global_{x}" for x in DIR_SUFFIXES]
    + [f"candidate_global_{x}" for x in DIR_SUFFIXES]
)


def stage_block():
    text = SNAKEFILE.read_text(encoding="utf-8")
    start = text.index("rule stage_06C1:")
    end = text.index("rule validate_artifacts:", start)
    return text[start:end]


def write_tsv(path, fieldnames, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def test_stage06c1_is_production_wired():
    block = stage_block()

    for expected in (
        'alignment_manifest=bundle("06A")',
        'full_dp_manifest=bundle("06C0")',
        "implementation_script=FINALIZE_DIRECTIONAL_GLOCAL_SCRIPT",
        '"scripts/run_stage_06C1.py"',
    ):
        assert expected in block

    assert "unimplemented_stage.py" not in block


def test_stage06c1_has_no_frozen_benchmark_counts():
    text = SCRIPT.read_text(encoding="utf-8")

    for forbidden in (
        "984070",
        "319396",
        "664674",
        "683785403044",
        "START-2023-01",
        "END-2026-03",
    ):
        assert forbidden not in text


def test_stage06c1_exact_pair_is_synthesized_without_dp(tmp_path):
    jobs = tmp_path / "jobs.tsv"
    nonexact = tmp_path / "nonexact.tsv"
    output = tmp_path / "output.tsv"
    summary = tmp_path / "summary.json"

    write_tsv(
        jobs,
        BASE_FIELDS + ["exact_sequence"],
        [
            {
                "release": "START-X",
                "target_id": "T1",
                "rid": "1",
                "job_scope": "PRIMARY",
                "discovery_channel": "SEQUENCE_ONLY",
                "target_optional_accession": "P1",
                "candidate_primary_accession": "P1",
                "target_length": "6",
                "candidate_length": "6",
                "exact_sequence": "1",
            }
        ],
    )

    write_tsv(
        nonexact,
        EXPECTED_FIELDS,
        [],
    )

    subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--alignment-jobs",
            str(jobs),
            "--nonexact-alignments",
            str(nonexact),
            "--output",
            str(output),
            "--summary",
            str(summary),
            "--start-release-id",
            "START-X",
            "--end-release-id",
            "END-X",
        ],
        check=True,
    )

    with output.open(
        "r",
        encoding="utf-8",
        newline="",
    ) as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))

    assert len(rows) == 1

    row = rows[0]

    assert row["target_global_status"] == "OK_EXACT"
    assert row["candidate_global_status"] == "OK_EXACT"

    assert row["target_global_score"] == "12"
    assert row["candidate_global_score"] == "12"

    assert row["target_global_target_coverage"] == "1.0000000000"
    assert row["candidate_global_candidate_coverage"] == "1.0000000000"

    assert row["target_global_matches"] == "6"
    assert row["candidate_global_matches"] == "6"

    assert row["target_global_cells_evaluated"] == "0"
    assert row["candidate_global_cells_evaluated"] == "0"

    data = json.loads(summary.read_text(encoding="utf-8"))

    assert data["stage"] == "06C1"
    assert data["canonical_pair_count"] == 1
    assert data["exact_pair_count"] == 1
    assert data["nonexact_pair_count"] == 0
    assert data["directional_alignment_count"] == 2
    assert data["nonexact_directional_full_dp_cell_count"] == 0


def test_stage06c1_strictly_consumes_nonexact_stream():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "NONEXACT stream/key mismatch" in text
    assert "NONEXACT table ended before job" in text
    assert "NONEXACT table contains extra row" in text
    assert "validate_base_against_job" in text


def test_stage06c1_preserves_frozen_exact_semantics():
    text = SCRIPT.read_text(encoding="utf-8")

    assert 'f"{prefix}_status": "OK_EXACT"' in text
    assert 'f"{prefix}_score": str(2 * n)' in text
    assert 'f"{prefix}_target_coverage": "1.0000000000"' in text
    assert 'f"{prefix}_candidate_coverage": "1.0000000000"' in text
    assert 'f"{prefix}_cells_evaluated": "0"' in text


def test_stage06c1_makes_no_same_entity_decision():
    text = SCRIPT.read_text(encoding="utf-8")

    assert '"same_entity_decision_made_here": False' in text


def test_stage06c1_has_empty_final_result_outcome():
    runner = RUNNER.read_text(encoding="utf-8")

    assert 'state="EMPTY_RESULT"' in runner
    assert 'reason_code="NO_CANONICAL_ALIGNMENT_PAIRS"' in runner


def test_stage06c1_completed_outcome():
    runner = RUNNER.read_text(encoding="utf-8")

    assert 'state="COMPLETED"' in runner
    assert "CANONICAL_DIRECTIONAL_ALIGNMENTS_FINALIZED" in runner
