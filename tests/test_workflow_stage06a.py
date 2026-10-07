from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAKEFILE = ROOT / "workflow/Snakefile"
RUNNER = ROOT / "workflow/scripts/run_stage_06A.py"
HELPER = ROOT / "scripts/pipeline/06A_prepare_alignment_jobs.py"


def stage_block() -> str:
    text = SNAKEFILE.read_text(encoding="utf-8")
    start = text.index("rule stage_06A:")
    end = text.index("rule stage_NONEXACT:", start)
    return text[start:end]


def test_stage06a_is_production_wired():
    block = stage_block()

    for expected in (
        'target_manifest=bundle("01")',
        'final_union_manifest=bundle("05A2")',
        'taxonomy_audit_manifest=bundle("05C2")',
        'start_store_manifest=bundle("05D", "START")',
        'end_store_manifest=bundle("05D", "END")',
        "implementation_script=ALIGNMENT_JOBS_SCRIPT",
        '"scripts/run_stage_06A.py"',
    ):
        assert expected in block

    assert "unimplemented_stage.py" not in block


def test_stage06a_does_not_perform_alignment_or_resolution():
    helper = HELPER.read_text(encoding="utf-8")

    assert '"alignment_made_here": False' in helper
    assert '"same_entity_decision_made_here": False' in helper
    assert '"exact_string_comparison_only": True' in helper

    for forbidden in (
        "gap_open",
        "gap_extend",
        "AFFINE_FULL_DP",
        "TARGET_GLOBAL",
        "CANDIDATE_GLOBAL",
    ):
        assert forbidden not in helper


def test_stage06a_uses_final_primary_and_final_taxonomy_audit():
    helper = HELPER.read_text(encoding="utf-8")
    runner = RUNNER.read_text(encoding="utf-8")

    assert '"05A2_final_candidate_union.tsv"' in runner
    assert '"05C2_taxonomy_audit.tsv"' in runner
    assert '"05D_candidate_sequences.tsv"' in runner

    assert '"audit_alignment_allowed"' in helper
    assert '"record_already_in_primary_union_for_target"' in helper


def test_stage06a_collapses_primary_audit_pair_overlap_without_losing_audit():
    helper = HELPER.read_text(encoding="utf-8")

    assert '"primary_taxonomy_audit_pair_overlap_emits_primary_only": True' in helper
    assert 'existing["job_scope"] != "PRIMARY"' in helper
    assert 'existing["audit_class"] = row["audit_class"]' in helper
    assert 'existing["audit_key_source_reason"] =' in helper


def test_stage06a_uses_generic_sequence_search_exact_name():
    helper = HELPER.read_text(encoding="utf-8")

    assert "sequence_search_exact_sequence" in helper
    assert "stage03_exact_sequence" not in helper


def test_stage06a_has_empty_and_nonempty_outcomes():
    runner = RUNNER.read_text(encoding="utf-8")

    assert 'state="EMPTY_RESULT"' in runner
    assert 'reason_code="NO_ALIGNMENT_JOBS"' in runner

    assert 'state="COMPLETED"' in runner
    assert 'reason_code="ALIGNMENT_JOBS_PREPARED"' in runner


def test_stage06a_has_no_sequence_search_policy():
    combined = (
        stage_block()
        + RUNNER.read_text(encoding="utf-8")
        + HELPER.read_text(encoding="utf-8")
    )

    assert "stage03_parameters" not in combined
    assert "candidate_policy" not in combined
    assert "target_distinct_7mers" not in combined
    assert "shared_distinct_7mers" not in combined
