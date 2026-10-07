from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAKEFILE = ROOT / "workflow/Snakefile"
RUNNER = ROOT / "workflow/scripts/run_stage_05R_POST.py"
HELPER = ROOT / "scripts/pipeline/05R_POST_report_unresolved_targets.py"


def stage_block() -> str:
    text = SNAKEFILE.read_text(encoding="utf-8")
    start = text.index("rule stage_05R_POST:")
    end = text.index("rule stage_05D:", start)
    return text[start:end]


def test_stage05r_post_is_production_wired():
    block = stage_block()

    for expected in (
        'uncovered_manifest=bundle("05B")',
        'start_r0_manifest=bundle("05R0", "START")',
        'end_r0_manifest=bundle("05R0", "END")',
        'start_r1_manifest=bundle("05R1", "START")',
        'end_r1_manifest=bundle("05R1", "END")',
        'final_union_manifest=bundle("05A2")',
        'taxonomy_audit_manifest=bundle("05C2")',
        'start_taxonomy_manifest=bundle("02B", "START")',
        'end_taxonomy_manifest=bundle("02B", "END")',
        'start_anchor_manifest=bundle("04B", "START")',
        'end_anchor_manifest=bundle("04B", "END")',
        "implementation_script=POST_RESCUE_REPORT_SCRIPT",
        '"scripts/run_stage_05R_POST.py"',
    ):
        assert expected in block

    assert "unimplemented_stage.py" not in block


def test_stage05r_post_is_report_only():
    helper = HELPER.read_text(encoding="utf-8")
    runner = RUNNER.read_text(encoding="utf-8")

    assert '"report_only": True' in helper
    assert '"candidate_universe_modified_here": False' in helper
    assert '"taxonomy_audit_modifies_primary_status": False' in helper
    assert '"same_entity_decision_made_here": False' in helper

    assert '"05A2_final_candidate_union.tsv"' in runner
    assert '"05C2_taxonomy_audit.tsv"' in runner


def test_stage05r_post_uses_upstream_rescue_facts():
    helper = HELPER.read_text(encoding="utf-8")

    assert '"kmer_sequence_searchable"' in helper
    assert "strict_rescue_selected" in helper
    assert "strict_rescue_pair_count" in helper
    assert "final_primary_candidate_count" in helper
    assert "UNRESOLVED_AFTER_STRICT_RESCUE" in helper

    assert "stage03_parameters" not in helper
    assert "candidate_policy" not in helper
    assert "target_distinct_7mers" not in helper
    assert "shared_distinct_7mers" not in helper


def test_stage05r_post_has_empty_and_nonempty_outcomes():
    runner = RUNNER.read_text(encoding="utf-8")

    assert 'state="EMPTY_RESULT"' in runner
    assert 'reason_code="NO_POST_RESCUE_UNRESOLVED_TARGETS"' in runner

    assert 'state="COMPLETED"' in runner
    assert 'reason_code="POST_RESCUE_UNRESOLVED_TARGETS_REPORTED"' in runner


def test_stage05r_post_does_not_consume_scientific_search_parameters():
    block = stage_block()
    runner = RUNNER.read_text(encoding="utf-8")

    assert 'cfg.data["scientific"]' not in block
    assert "stage03_parameters" not in runner
    assert "candidate_policy" not in runner
