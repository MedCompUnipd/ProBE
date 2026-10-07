"""Structured outcomes for technically successful workflow stages."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path

OUTCOME_STATES = frozenset(
    {
        "COMPLETED",
        "EMPTY_RESULT",
        "SKIPPED_EMPTY_INPUT",
        "SKIPPED_NO_ELIGIBLE_TARGETS",
        "TERMINAL_EMPTY",
    }
)

_REASON_CODE = re.compile(r"[A-Z][A-Z0-9_]*")


def stage_outcome(
    *,
    state: str,
    reason_code: str,
    message: str,
    counts: Mapping[str, int],
) -> dict[str, object]:
    """Build and validate a structured non-error stage outcome."""

    if state not in OUTCOME_STATES:
        raise ValueError(f"unknown stage outcome state {state!r}")

    if not isinstance(reason_code, str) or not _REASON_CODE.fullmatch(reason_code):
        raise ValueError(f"invalid stage outcome reason_code {reason_code!r}")

    if not isinstance(message, str) or not message.strip():
        raise ValueError("stage outcome message must be nonempty")

    normalized_counts: dict[str, int] = {}
    for key, value in counts.items():
        if not isinstance(key, str) or not key:
            raise ValueError("stage outcome count names must be nonempty strings")
        if type(value) is not int or value < 0:
            raise ValueError(
                f"stage outcome count {key!r} must be a nonnegative integer"
            )
        normalized_counts[key] = value

    return {
        "schema_version": 1,
        "state": state,
        "reason_code": reason_code,
        "message": message,
        "counts": normalized_counts,
    }


def write_stage_outcome(
    path: Path,
    *,
    state: str,
    reason_code: str,
    message: str,
    counts: Mapping[str, int],
) -> None:
    """Write one canonical stage_outcome.json artifact."""

    payload = stage_outcome(
        state=state,
        reason_code=reason_code,
        message=message,
        counts=counts,
    )

    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
