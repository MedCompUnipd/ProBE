from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAKEFILE = ROOT / "workflow/Snakefile"
RUNNER = ROOT / "workflow/scripts/run_stage_05D.py"
CPP = ROOT / "cpp/src/probe_candidate_sequence_extract_v1.cpp"
CMAKE = ROOT / "cpp/CMakeLists.txt"
PIXI = ROOT / "pixi.toml"


def stage_block() -> str:
    text = SNAKEFILE.read_text(encoding="utf-8")
    start = text.index("rule stage_05D:")
    end = text.index("rule stage_06A:", start)
    return text[start:end]


def test_stage05d_is_production_wired():
    block = stage_block()

    assert 'final_union_manifest=bundle("05A2")' in block
    assert 'taxonomy_audit_manifest=bundle("05C2")' in block
    assert 'full_v2_manifest=bundle("01A_FULL", "{role}")' in block
    assert "binary=CANDIDATE_SEQUENCE_EXTRACT_BINARY" in block
    assert '"scripts/run_stage_05D.py"' in block
    assert "unimplemented_stage.py" not in block

    assert 'cfg.data["scientific"]' not in block
    assert "candidate_policy" not in block
    assert "stage03_parameters" not in block


def test_stage05d_native_tool_is_buildable():
    snakefile = SNAKEFILE.read_text(encoding="utf-8")
    cmake = CMAKE.read_text(encoding="utf-8")
    pixi = PIXI.read_text(encoding="utf-8")

    assert CPP.is_file()

    assert "CANDIDATE_SEQUENCE_EXTRACT_BINARY" in snakefile
    assert "rule build_candidate_sequence_extract:" in snakefile
    assert "probe-candidate-sequence-extract-v1" in cmake
    assert "build-candidate-sequence-extract" in pixi


def test_stage05d_materializes_only_primary_and_alignable_audit_requests():
    runner = RUNNER.read_text(encoding="utf-8")
    cpp = CPP.read_text(encoding="utf-8")

    assert '"05A2_final_candidate_union.tsv"' in runner
    assert '"05C2_taxonomy_audit.tsv"' in runner
    assert '"01A_full_v2.fasta"' in runner

    assert '"audit_alignment_allowed"' in runner
    assert 'allowed != "1"' in runner

    assert '"needed_by_primary"' in runner
    assert '"needed_by_taxonomy_audit"' in runner

    assert "full-V2 metadata mismatch" in cpp
    assert "RID/record ordinal mismatch" in cpp
    assert "sequence length mismatch RID=" in cpp


def test_stage05d_has_empty_input_failover():
    runner = RUNNER.read_text(encoding="utf-8")

    assert 'state="SKIPPED_EMPTY_INPUT"' in runner
    assert 'reason_code="NO_CANDIDATE_SEQUENCES_REQUESTED"' in runner
    assert '"command_executed": bool(requested_rids)' in runner


def test_stage05d_completed_outcome_is_explicit():
    runner = RUNNER.read_text(encoding="utf-8")

    assert 'state="COMPLETED"' in runner
    assert 'reason_code="CANDIDATE_SEQUENCES_MATERIALIZED"' in runner


def test_stage05d_has_no_sequence_search_policy():
    combined = RUNNER.read_text(encoding="utf-8") + CPP.read_text(encoding="utf-8")

    assert "stage03_parameters" not in combined
    assert "candidate_policy" not in combined
    assert "target_distinct_7mers" not in combined
    assert "shared_distinct_7mers" not in combined
