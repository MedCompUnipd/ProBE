"""Stage 07 verifies certified cell counts without relaxing geometry checks.

The supplied count map represents an independently checked plan. These
tests do not certify a search region, execute DP or assert performance.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_stage07_resolver_alignments import (
    ALIGN_FIELDS,
    JOB_FIELDS,
    KEY,
    alignment,
    job,
    record,
    write,
)

from probe.resolver_alignments import collect_alignments


def _inputs(tmp_path: Path, *, exact: bool = False) -> dict:
    candidate = record()
    planned = job(candidate, n=5, m=5, exact=exact)
    measured = alignment(planned, "exact" if exact else "variant")
    if not exact:
        for side, cells in ("target_global", 20), ("candidate_global", 25):
            measured[f"{side}_matches"] = "4"
            measured[f"{side}_mismatches"] = "1"
            measured[f"{side}_cells_evaluated"] = str(cells)
    return {
        "candidates": {KEY: candidate},
        "jobs_path": write(tmp_path / "06A.tsv", JOB_FIELDS, [planned]),
        "alignments_path": write(tmp_path / "06C1.tsv", ALIGN_FIELDS, [measured]),
    }


def test_certified_counts_are_checked_in_their_original_directions(
    tmp_path: Path,
) -> None:
    joined = collect_alignments(
        **_inputs(tmp_path),
        expected_cells={KEY: (20, 25)},
    )[KEY]
    assert joined.aligned
    assert joined.exact_sequence is False
    assert joined.target_length == joined.candidate_length == 5
    assert joined.equal_length_variant
    assert not joined.exact_subsequence
    assert joined.target_global.cells == 20
    assert joined.candidate_global.cells == 25
    for side in (joined.target_global, joined.candidate_global):
        assert side.status == "OK"
        assert side.matches == 4
        assert side.mismatches == 1
        assert side.paired == side.columns == 5
        assert side.gap_in_target == side.gap_in_candidate == 0


def test_no_certified_plan_keeps_full_rectangle_requirement(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="DP-cell mismatch"):
        collect_alignments(**_inputs(tmp_path))


def test_missing_nonexact_plan_entry_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="missing certified DP-cell plan"):
        collect_alignments(**_inputs(tmp_path), expected_cells={})


def test_extra_plan_entry_is_rejected(tmp_path: Path) -> None:
    plan = {KEY: (20, 25), (KEY[0], KEY[1], "999"): (20, 25)}
    with pytest.raises(ValueError, match="missing or extra pairs"):
        collect_alignments(**_inputs(tmp_path), expected_cells=plan)


@pytest.mark.parametrize("counts", [(20,), (20, 25, 30)])
def test_plan_requires_both_and_only_two_directions(
    tmp_path: Path,
    counts: tuple[int, ...],
) -> None:
    with pytest.raises(ValueError, match="missing certified DP-cell plan"):
        collect_alignments(**_inputs(tmp_path), expected_cells={KEY: counts})


@pytest.mark.parametrize("counts", [(19, 25), (20, 24), (25, 20)])
def test_measured_counts_cannot_be_swapped_or_fudged(
    tmp_path: Path,
    counts: tuple[int, int],
) -> None:
    with pytest.raises(ValueError, match="DP-cell mismatch"):
        collect_alignments(**_inputs(tmp_path), expected_cells={KEY: counts})


@pytest.mark.parametrize("counts", [(0, 25), (20, 37), (20, True), (20, 25.0)])
def test_certified_counts_must_be_integers_inside_the_full_rectangle(
    tmp_path: Path,
    counts: tuple[object, object],
) -> None:
    with pytest.raises(ValueError, match="invalid certified DP-cell expectation"):
        collect_alignments(**_inputs(tmp_path), expected_cells={KEY: counts})


def test_exact_pair_cannot_appear_in_the_nonexact_cell_plan(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="missing or extra pairs"):
        collect_alignments(
            **_inputs(tmp_path, exact=True),
            expected_cells={KEY: (0, 0)},
        )


def test_exact_only_input_has_an_empty_nonexact_plan(tmp_path: Path) -> None:
    joined = collect_alignments(**_inputs(tmp_path, exact=True), expected_cells={})
    assert joined[KEY].exact_sequence
    assert joined[KEY].target_global.cells == 0
    assert joined[KEY].candidate_global.cells == 0
