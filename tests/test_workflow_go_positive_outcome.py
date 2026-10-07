from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "workflow/scripts/run_stage_01C.py"


def test_stage01c_records_nonempty_and_empty_filter_outcomes():
    text = RUNNER.read_text(encoding="utf-8")

    assert "from probe.workflow.outcome import write_stage_outcome" in text
    assert '"stage_outcome.json"' in text

    assert '"positive_whitelist_count"' in text
    assert '"output_v2_record_count"' in text
    assert '"whitelist_missing_from_v2"' in text

    assert 'state="EMPTY_RESULT"' in text
    assert 'reason_code="NO_POSITIVE_GOA_ACCESSIONS"' in text

    assert 'state="COMPLETED"' in text
    assert 'reason_code="GO_POSITIVE_RECORDS_EMITTED"' in text


def test_stage01c_keeps_filter_contract_violations_hard():
    text = RUNNER.read_text(encoding="utf-8")

    assert "if missing_count != 0:" in text
    assert "if output_count != positive_count:" in text
    assert "01C stats metric contract violation" in text

    assert "SKIPPED_" not in text


def test_stage01c_always_executes_filter_binary():
    text = RUNNER.read_text(encoding="utf-8")

    subprocess_pos = text.index("subprocess.run(")
    empty_outcome_pos = text.index('state="EMPTY_RESULT"')

    assert subprocess_pos < empty_outcome_pos
