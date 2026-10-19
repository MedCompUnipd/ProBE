"""Check that the canonical DAG wiring does not simulate donor approvals."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

import pytest

from probe.canonical_v2.bounded_dp import BACKEND
from probe.canonical_v2.dag_contract import destination
from probe.canonical_v2.dag_execution import dispatch


def test_resolver_cannot_run_in_prepare_mode(tmp_path: Path) -> None:
    from test_canonical_v2_dag_contract import _spec

    spec = _spec(tmp_path)
    with pytest.raises(ValueError, match="mode=execute"):
        dispatch(
            spec=spec,
            stage="stage07",
            target=destination(spec, "stage07"),
        )


def test_node_cannot_publish_to_another_policy_path(tmp_path: Path) -> None:
    from test_canonical_v2_dag_contract import _spec

    spec = _spec(tmp_path)
    spec["mode"] = "execute"
    with pytest.raises(ValueError, match="destination differs"):
        dispatch(
            spec=spec,
            stage="stage07",
            target=Path(spec["output_root"]) / "other",
        )
    assert not Path(spec["output_root"]).exists()


def test_dispatch_rejects_legacy_schema_before_creating_output(tmp_path: Path) -> None:
    from test_canonical_v2_dag_contract import _spec

    spec = _spec(tmp_path)
    spec["schema"] = "probe-canonical-v2-dag-input-v1"
    spec["mode"] = "execute"
    with pytest.raises(ValueError, match="contract version"):
        dispatch(
            spec=spec,
            stage="alignment",
            target=Path(spec["output_root"]) / "old-contract",
        )
    assert not Path(spec["output_root"]).exists()


def test_alignment_dispatch_binds_documentary_release_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_canonical_v2_dag_contract import _spec

    from probe.canonical_v2 import alignment_v2

    spec = _spec(tmp_path)
    spec["mode"] = "execute"
    captured = []

    def capture_execution(**arguments):
        captured.append(arguments)

    monkeypatch.setattr(alignment_v2, "execute_alignment_v2", capture_execution)
    dispatch(
        spec=spec,
        stage="alignment",
        target=destination(spec, "alignment"),
    )
    assert len(captured) == 1
    arguments = captured[0]
    assert arguments["dp_backend"] == BACKEND
    assert arguments["dp_backend"] == spec["scientific_contracts"]["dp_backend"]
    assert arguments["lineage_index"] == Path(spec["inputs"]["lineage_index"])
    assert arguments["lineage_paths"] == {
        "R1": Path(spec["per_release"]["START"]["lineage"]),
        "R2": Path(spec["per_release"]["END"]["lineage"]),
    }
    assert arguments["taxonomy"] == {
        role: Path(spec["per_release"][role]["taxonomy"]) for role in ("START", "END")
    }


@pytest.mark.parametrize(
    "missing",
    ("--lineage-index", "--start-lineage", "--end-lineage"),
)
def test_alignment_cli_requires_documentary_inputs_before_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    missing: str,
) -> None:
    script = (
        Path(__file__).resolve().parents[1]
        / "scripts/pipeline/06_execute_canonical_v2_alignments.py"
    )
    arguments = [str(script)]
    for flag in (
        "--workload",
        "--targets",
        "--start-taxonomy",
        "--end-taxonomy",
        "--lineage-index",
        "--start-lineage",
        "--end-lineage",
        "--nonexact-script",
        "--full-dp-binary",
        "--finalize-script",
        "--destination",
    ):
        if flag != missing:
            arguments.extend((flag, str(tmp_path / flag.removeprefix("--"))))
    monkeypatch.setattr(sys, "argv", arguments)
    with pytest.raises(SystemExit) as stopped:
        runpy.run_path(str(script), run_name="__main__")
    assert stopped.value.code == 2
    assert missing in capsys.readouterr().err
    assert not (tmp_path / "destination").exists()
