from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "workflow/scripts/run_stage_01A_FULL.py"


def test_stage01a_full_rejects_zero_record_reference_release():
    text = RUNNER.read_text(encoding="utf-8")

    assert 'summary.get("release_id") != release_id' in text
    assert 'totals = summary.get("totals")' in text
    assert 'records_written = totals.get("records_written")' in text
    assert "records_written < 0" in text
    assert "records_written == 0" in text
    assert "FULL V2 reference release is unusable" in text


def test_stage01a_full_does_not_treat_zero_reference_as_empty_outcome():
    text = RUNNER.read_text(encoding="utf-8")

    assert "write_stage_outcome" not in text
    assert "EMPTY_RESULT" not in text
    assert "SKIPPED_" not in text
