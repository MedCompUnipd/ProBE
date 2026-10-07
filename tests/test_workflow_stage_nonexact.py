from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAKEFILE = ROOT / "workflow/Snakefile"
RUNNER = ROOT / "workflow" / "scripts" / "run_stage_NONEXACT.py"
HELPER = ROOT / "scripts" / "pipeline" / "NONEXACT_select_alignment_jobs.py"


def stage_block() -> str:
    text = SNAKEFILE.read_text(encoding="utf-8")

    start = text.index("rule stage_NONEXACT:")
    end = text.index(
        "rule stage_06C0:",
        start,
    )

    return text[start:end]


def test_nonexact_is_production_wired():
    block = stage_block()

    assert 'alignment_manifest=bundle("06A")' in block
    assert "implementation_script=NONEXACT_SELECTION_SCRIPT" in block
    assert '"scripts/run_stage_NONEXACT.py"' in block

    assert "unimplemented_stage.py" not in block


def test_nonexact_is_selection_only():
    helper = HELPER.read_text(encoding="utf-8")

    assert '"selection_only": True' in helper
    assert '"alignment_made_here": False' in helper
    assert '"scoring_made_here": False' in helper
    assert '"same_entity_decision_made_here": False' in helper


def test_nonexact_selects_exact_sequence_zero():
    helper = HELPER.read_text(encoding="utf-8")

    assert '"exact_sequence"' in helper
    assert 'if exact == "1":' in helper
    assert "writer.writerow(row)" in helper


def test_nonexact_preserves_06a_rows():
    helper = HELPER.read_text(encoding="utf-8")

    assert '"nonexact_rows_preserved_from_06A": True' in helper
    assert "fieldnames=reader.fieldnames" in helper


def test_nonexact_validates_sorted_unique_input():
    helper = HELPER.read_text(encoding="utf-8")

    assert "previous_key" in helper
    assert "key <= previous_key" in helper
    assert "strictly sorted/unique" in helper


def test_nonexact_has_three_operational_outcomes():
    runner = RUNNER.read_text(encoding="utf-8")

    assert 'state="SKIPPED_EMPTY_INPUT"' in runner
    assert 'reason_code="NO_ALIGNMENT_JOBS"' in runner

    assert 'state="EMPTY_RESULT"' in runner
    assert 'reason_code="ALL_ALIGNMENT_JOBS_EXACT"' in runner

    assert 'state="COMPLETED"' in runner
    assert 'reason_code="NONEXACT_JOBS_SELECTED"' in runner


def test_nonexact_has_no_alignment_policy():
    combined = (
        stage_block()
        + RUNNER.read_text(encoding="utf-8")
        + HELPER.read_text(encoding="utf-8")
    )

    for forbidden in (
        "stage03_parameters",
        "candidate_policy",
        "gap_open",
        "gap_extend",
        "AFFINE_FULL_DP",
        "TARGET_GLOBAL",
        "CANDIDATE_GLOBAL",
    ):
        assert forbidden not in combined
