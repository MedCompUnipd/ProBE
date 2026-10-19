"""B3 producer output is accepted by the validated Stage 2C preview reader."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from probe.canonical_v2.interop import publish_completion_input
from probe.canonical_v2.stage05_06a import PRIMARY, build_stage05_06a
from probe.stage2c_alignment_completion import _jobs, _preview
from probe.stage2c_alignment_preview import JOBS_OUT, UNION_OUT

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "canonical_b3_fixture", ROOT / "tests/test_canonical_v2_stage05_06a.py"
)
assert SPEC is not None and SPEC.loader is not None
FIXTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIXTURE)


def test_actual_b3_candidate_universe_passes_handoff(tmp_path: Path) -> None:
    spec = FIXTURE._fixture(tmp_path)
    build_stage05_06a(**spec)
    preview_dir = tmp_path / "handoff"
    publish_completion_input(
        b3=spec["output_dir"],
        releases=spec["releases"],
        destination=preview_dir,
    )
    metadata, files = _preview(preview_dir)
    assert metadata["canonical_rows_preserved"] is True
    assert (spec["output_dir"] / PRIMARY).read_bytes() == (
        preview_dir / UNION_OUT
    ).read_bytes()
    total, exact, nonexact = _jobs(files[JOBS_OUT], {"R1", "R2"})
    assert (total, exact, nonexact) == (4, 3, 1)
