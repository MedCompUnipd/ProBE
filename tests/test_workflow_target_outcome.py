from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "workflow/scripts/run_stage_01.py"


def test_stage01_records_empty_and_completed_target_outcomes():
    text = RUNNER.read_text(encoding="utf-8")

    assert "from probe.workflow.outcome import write_stage_outcome" in text
    assert 'staging / "stage_outcome.json"' in text

    assert 'state="EMPTY_RESULT"' in text
    assert 'reason_code="NO_ACCEPTED_TARGETS"' in text

    assert 'state="COMPLETED"' in text
    assert 'reason_code="TARGETS_ACCEPTED"' in text

    assert "result.accepted_count == 0" in text
    assert '"accepted_targets": result.accepted_count' in text
    assert '"rejected_targets": result.rejected_count' in text
