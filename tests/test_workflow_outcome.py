import json

import pytest

from probe.workflow.outcome import stage_outcome, write_stage_outcome


def test_stage_outcome_roundtrip(tmp_path):
    path = tmp_path / "stage_outcome.json"

    write_stage_outcome(
        path,
        state="EMPTY_RESULT",
        reason_code="NO_SEQUENCE_CANDIDATES",
        message="The scan completed successfully but emitted no candidate pairs.",
        counts={
            "input_targets": 10,
            "eligible_targets": 8,
            "output_pairs": 0,
        },
    )

    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload == {
        "schema_version": 1,
        "state": "EMPTY_RESULT",
        "reason_code": "NO_SEQUENCE_CANDIDATES",
        "message": ("The scan completed successfully but emitted no candidate pairs."),
        "counts": {
            "eligible_targets": 8,
            "input_targets": 10,
            "output_pairs": 0,
        },
    }


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        (
            {
                "state": "BROKEN",
                "reason_code": "NO_RESULTS",
                "message": "message",
                "counts": {},
            },
            "unknown stage outcome state",
        ),
        (
            {
                "state": "EMPTY_RESULT",
                "reason_code": "not-valid",
                "message": "message",
                "counts": {},
            },
            "reason_code",
        ),
        (
            {
                "state": "EMPTY_RESULT",
                "reason_code": "NO_RESULTS",
                "message": "",
                "counts": {},
            },
            "message",
        ),
        (
            {
                "state": "EMPTY_RESULT",
                "reason_code": "NO_RESULTS",
                "message": "message",
                "counts": {"bad": -1},
            },
            "nonnegative integer",
        ),
    ],
)
def test_stage_outcome_rejects_invalid_values(kwargs, match):
    with pytest.raises(ValueError, match=match):
        stage_outcome(**kwargs)
