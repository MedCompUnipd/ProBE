from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAKEFILE = ROOT / "workflow/Snakefile"
RUNNER = ROOT / "workflow/scripts/run_stage_06C0.py"
CPP = ROOT / "cpp" / "src" / "probe_directional_glocal_full_dp_v1.cpp"
CMAKE = ROOT / "cpp/CMakeLists.txt"


def stage_block():
    text = SNAKEFILE.read_text(encoding="utf-8")
    start = text.index("rule stage_06C0:")
    end = text.index("rule stage_06C1:", start)
    return text[start:end]


def test_stage06c0_is_production_wired():
    block = stage_block()

    for expected in (
        'target_manifest=bundle("01")',
        'nonexact_manifest=bundle("NONEXACT")',
        'start_store_manifest=bundle("05D", "START")',
        'end_store_manifest=bundle("05D", "END")',
        "binary=DIRECTIONAL_GLOCAL_BINARY",
        '"scripts/run_stage_06C0.py"',
    ):
        assert expected in block

    assert "unimplemented_stage.py" not in block


def test_stage06c0_native_target_uses_openmp():
    cmake = CMAKE.read_text(encoding="utf-8")

    assert "find_package(OpenMP REQUIRED)" in cmake
    assert "probe-directional-glocal-full-dp-v1" in cmake
    assert "OpenMP::OpenMP_CXX" in cmake


def test_stage06c0_preserves_frozen_scoring():
    cpp = CPP.read_text(encoding="utf-8")

    assert "constexpr int MATCH_SCORE = 2;" in cpp
    assert "constexpr int MISMATCH_SCORE = -1;" in cpp
    assert "constexpr int GAP_OPEN = -3;" in cpp
    assert "constexpr int GAP_EXTEND = -1;" in cpp


def test_stage06c0_preserves_directional_glocal_modes():
    cpp = CPP.read_text(encoding="utf-8")

    assert "TARGET_GLOBAL" in cpp
    assert "CANDIDATE_GLOBAL" in cpp

    assert "TARGET_GLOBAL invariant violated" in cpp
    assert "CANDIDATE_GLOBAL invariant violated" in cpp


def test_stage06c0_requires_nonexact_jobs_only():
    cpp = CPP.read_text(encoding="utf-8")
    runner = RUNNER.read_text(encoding="utf-8")

    assert 'f[i_exact] != "0"' in cpp
    assert 'row["exact_sequence"] != "0"' in runner


def test_stage06c0_uses_dynamic_release_ids():
    cpp = CPP.read_text(encoding="utf-8")
    runner = RUNNER.read_text(encoding="utf-8")

    assert "--start-release-id" in cpp
    assert "--end-release-id" in cpp

    assert "START-2023-01" not in cpp
    assert "END-2026-03" not in cpp

    assert "start_release_id" in runner
    assert "end_release_id" in runner


def test_stage06c0_has_zero_input_failover():
    runner = RUNNER.read_text(encoding="utf-8")

    assert 'state="SKIPPED_EMPTY_INPUT"' in runner
    assert 'reason_code="NO_NONEXACT_ALIGNMENT_JOBS"' in runner
    assert '"command_executed": job_count > 0' in runner


def test_stage06c0_has_completed_outcome():
    runner = RUNNER.read_text(encoding="utf-8")

    assert 'state="COMPLETED"' in runner
    assert "NONEXACT_DIRECTIONAL_ALIGNMENTS_COMPUTED" in runner


def test_stage06c0_rejects_non_frozen_alignment_policy():
    runner = RUNNER.read_text(encoding="utf-8")

    for expected in (
        '"algorithm": "AFFINE_FULL_DP"',
        '"banding": False',
        '"match": 2',
        '"mismatch": -1',
        '"gap_open": -3',
        '"gap_extend": -1',
    ):
        assert expected in runner


def test_stage06c0_makes_no_same_entity_decision():
    cpp = CPP.read_text(encoding="utf-8")
    runner = RUNNER.read_text(encoding="utf-8")

    assert "SAME_ENTITY decision" in cpp
    assert "same_entity_decision" not in runner
