from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAKEFILE = ROOT / "workflow/Snakefile"
RUNNER = ROOT / "workflow/scripts/run_stage_05C2.py"
HELPER = ROOT / "scripts/pipeline/05C2_freeze_taxonomy_audit_channel.py"


def stage_block(name: str, next_name: str) -> str:
    text = SNAKEFILE.read_text(encoding="utf-8")
    start = text.index(f"rule {name}:")
    end = text.index(f"rule {next_name}:", start)
    return text[start:end]


def test_stage05c2_is_production_wired():
    block = stage_block("stage_05C2", "stage_05R_POST")

    assert 'final_union_manifest=bundle("05A2")' in block
    assert 'start_anchor_manifest=bundle("04B", "START")' in block
    assert 'end_anchor_manifest=bundle("04B", "END")' in block
    assert "implementation_script=FINAL_TAXONOMY_AUDIT_SCRIPT" in block
    assert '"scripts/run_stage_05C2.py"' in block
    assert "unimplemented_stage.py" not in block

    assert "stage03_parameters" not in block
    assert "candidate_policy" not in block
    assert 'cfg.data["scientific"]' not in block


def test_stage05c2_uses_final_union_and_dynamic_releases():
    runner = RUNNER.read_text(encoding="utf-8")
    helper = HELPER.read_text(encoding="utf-8")

    assert '"05A2_final_candidate_union.tsv"' in runner

    assert '"--start-release-id"' in helper
    assert '"--end-release-id"' in helper

    assert "START-2023-01" not in helper
    assert "END-2026-03" not in helper


def test_stage05c2_preserves_audit_policy():
    text = HELPER.read_text(encoding="utf-8")

    assert '"TAXONOMY_RESCUE_AUDIT", 1' in text
    assert '"CROSS_SPECIES_AUDIT", 0' in text
    assert '"UNRESOLVED_TAXONOMY_AUDIT", 0' in text

    runner = RUNNER.read_text(encoding="utf-8")

    assert '"primary_candidate_universe_modified_here": False' in runner
    assert '"same_species_wrong_subtaxon_alignment_audit_allowed": True' in runner
    assert '"same_species_wrong_subtaxon_becomes_primary_candidate": False' in runner
    assert '"cross_species_sequence_test_allowed": False' in runner
    assert '"same_entity_decision_made_here": False' in runner


def test_stage05c2_records_empty_and_nonempty_outcomes():
    text = RUNNER.read_text(encoding="utf-8")

    assert 'state="EMPTY_RESULT"' in text
    assert 'reason_code="NO_TAXONOMY_AUDIT_ROWS"' in text

    assert 'state="COMPLETED"' in text
    assert 'reason_code="TAXONOMY_AUDIT_ROWS_EMITTED"' in text

    assert '"stage_outcome.json"' in text


def test_stage05c2_has_no_sequence_search_parameters():
    text = RUNNER.read_text(encoding="utf-8")
    helper = HELPER.read_text(encoding="utf-8")

    combined = text + helper

    assert "stage03_parameters" not in combined
    assert "candidate_policy" not in combined
    assert "target_distinct_7mers" not in combined
    assert "shared_distinct_7mers" not in combined
