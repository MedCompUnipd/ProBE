from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "workflow/scripts/run_stage_01B1.py"


def test_stage01b1_records_positive_and_empty_goa_outcomes():
    text = RUNNER.read_text(encoding="utf-8")

    assert "from probe.workflow.outcome import write_stage_outcome" in text
    assert '"stage_outcome.json"' in text

    assert 'stats.get("positive_accession_count")' in text

    assert 'state="EMPTY_RESULT"' in text
    assert 'reason_code="NO_POSITIVE_GOA_ACCESSIONS"' in text

    assert 'state="COMPLETED"' in text
    assert 'reason_code="POSITIVE_GOA_ACCESSIONS_RETAINED"' in text


def test_stage01b1_keeps_malformed_summary_as_hard_failure():
    text = RUNNER.read_text(encoding="utf-8")

    assert "positive_accession_count < 0" in text
    assert "01B1 summary has invalid positive_accession_count" in text
    assert "SKIPPED_" not in text
