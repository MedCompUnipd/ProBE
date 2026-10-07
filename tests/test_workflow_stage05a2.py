from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAKEFILE = ROOT / "workflow/Snakefile"
RUNNER = ROOT / "workflow/scripts/run_stage_05A2.py"
HELPER = ROOT / "scripts/pipeline/05A2_finalize_candidate_union.py"


def stage_block(name: str, next_name: str) -> str:
    text = SNAKEFILE.read_text(encoding="utf-8")
    start = text.index(f"rule {name}:")
    end = text.index(f"rule {next_name}:", start)
    return text[start:end]


def test_stage05a2_is_production_wired():
    block = stage_block("stage_05A2", "stage_05C2")

    assert 'baseline_manifest=bundle("05A")' in block
    assert 'start_rescue_manifest=bundle("05R1", "START")' in block
    assert 'end_rescue_manifest=bundle("05R1", "END")' in block
    assert "implementation_script=FINAL_CANDIDATE_UNION_SCRIPT" in block
    assert '"scripts/run_stage_05A2.py"' in block
    assert "unimplemented_stage.py" not in block

    assert 'cfg.data["scientific"]' not in block
    assert 'cfg.data["ontology"]' not in block
    assert "stage03_parameters" not in block
    assert "candidate_policy" not in block


def test_stage05a2_helper_uses_dynamic_release_ids():
    text = HELPER.read_text(encoding="utf-8")

    assert '"--start-release-id"' in text
    assert '"--end-release-id"' in text

    assert "START-2023-01" not in text
    assert "END-2026-03" not in text


def test_stage05a2_preserves_union_invariants():
    helper = HELPER.read_text(encoding="utf-8")
    runner = RUNNER.read_text(encoding="utf-8")

    assert "strict rescue overlaps baseline candidate" in helper
    assert "FULL-V2 record_number/RID mismatch" in helper
    assert '"release",' in helper
    assert '"target_id",' in helper
    assert '"rid",' in helper

    assert "candidate-union arithmetic invariant violated" in runner
    assert "baseline_overlap_with_strict_rescue" in runner
    assert "metadata_conflicts_detected" in runner


def test_stage05a2_records_empty_and_nonempty_outcomes():
    text = RUNNER.read_text(encoding="utf-8")

    assert 'state="EMPTY_RESULT"' in text
    assert 'reason_code="NO_FINAL_CANDIDATES"' in text

    assert 'state="COMPLETED"' in text
    assert 'reason_code="FINAL_CANDIDATE_UNION_EMITTED"' in text

    assert '"stage_outcome.json"' in text


def test_stage05a2_uses_generic_kmer_evidence_names():
    text = HELPER.read_text(encoding="utf-8")

    assert '"target_distinct_kmers"' in text
    assert '"shared_distinct_kmers"' in text
    assert '"shared_unique_positional_kmers"' in text

    assert "target_distinct_7mers" not in text
    assert "shared_distinct_7mers" not in text
    assert "shared_unique_positional_7mers" not in text
