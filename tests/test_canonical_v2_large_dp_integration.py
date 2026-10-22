"""Automatic exact-DP wiring through the existing scientific stage contracts."""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from canonical_v2_fresh_raw_fixture import create_raw_fixture
from test_canonical_v2_bounded_execution import _execution_inputs

from probe.canonical_v2.alignment_v2 import BOUNDED_SCHEMA, execute_alignment_v2
from probe.canonical_v2.bounded_dp import BACKEND, PLAN
from probe.canonical_v2.dag_contract import (
    discovery_fingerprint,
    large_dp_arguments,
    read_dag_spec,
    validate_large_dp,
)
from probe.canonical_v2.fresh_dag import (
    canonical_sources,
    content_identity,
    node_dependencies,
    read_recipe,
)
from probe.canonical_v2.large_dp import BACKEND as AUTO_BACKEND
from probe.canonical_v2.large_dp import LINEAR_BACKEND, ROUTING
from probe.stage2c_alignment_completion import (
    DP_OUTPUT,
    FINAL_OUTPUT,
    NONEXACT,
    POLICY_SKIP,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def linear_binary(tmp_path_factory: pytest.TempPathFactory) -> Path:
    binary = tmp_path_factory.mktemp("linear-integration") / "linear-dp"
    compiler = shutil.which("g++")
    assert compiler is not None
    subprocess.run(
        [
            compiler,
            "-O2",
            "-std=c++17",
            str(ROOT / "cpp/src/probe_directional_glocal_linear_dp_v1.cpp"),
            "-o",
            str(binary),
        ],
        check=True,
    )
    return binary


def test_forced_large_pair_preserves_admission_and_finalizer(
    tmp_path: Path, bounded_binary: Path, linear_binary: Path
) -> None:
    reference_root = tmp_path / "reference"
    automatic_root = tmp_path / "automatic"
    reference_root.mkdir()
    automatic_root.mkdir()
    reference = _execution_inputs(reference_root, bounded_binary)
    automatic = _execution_inputs(automatic_root, bounded_binary)
    expected = execute_alignment_v2(**reference)
    automatic.update(
        linear_dp_binary=linear_binary,
        max_pair_cells=1,
        max_total_cells=7,
        max_chunk_cells=7,
        dp_checkpoint_rows=1,
        dp_scratch_root=tmp_path / "scratch",
    )
    actual = execute_alignment_v2(**automatic)
    assert actual["schema"] == expected["schema"] == BOUNDED_SCHEMA
    assert actual["dp_backend"] == expected["dp_backend"] == BACKEND
    assert actual["dp_execution_backend"] == AUTO_BACKEND
    assert actual["dp_gate"] == expected["dp_gate"]
    assert actual["dp_cells"] == expected["dp_cells"]
    for name in (DP_OUTPUT, FINAL_OUTPUT, NONEXACT, POLICY_SKIP, PLAN):
        assert (reference["destination"] / name).read_bytes() == (
            automatic["destination"] / name
        ).read_bytes(), name
    assert ROUTING in actual["output_sha256"]
    assert actual["certified_plan"]["backend_pair_counts"] == {
        LINEAR_BACKEND: actual["dp_gate"]["selected"]
    }
    assert "linear_dp_binary" in actual["inputs_sha256"]
    assert list((tmp_path / "scratch/checkpoints").glob("*/checkpoint.json"))


def test_optional_recipe_resources_pin_engine_without_changing_science(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "source"
    repo.mkdir()
    recipe_path = create_raw_fixture(
        repo=repo, run=tmp_path / "run", dp_binary=repo / "bounded-dp"
    )
    recipe = read_recipe(recipe_path, expected_repo=repo)
    plans = {
        kind: {
            "paths": {
                role: str(recipe.run / "work" / kind / role)
                for role in ("START", "END")
            }
        }
        for kind in ("species", "full_exact", "union")
    }
    before = canonical_sources(recipe, plans)
    old_fingerprint = discovery_fingerprint(before)
    assert large_dp_arguments(before) == {}
    for name in recipe.data["tools"].values():
        path = Path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic tool\n")
    linear = repo / "linear-dp"
    linear.write_text("first exact engine\n")
    option = {
        "binary": str(linear),
        "max_dp_memory_bytes": 536_870_912,
        "checkpoint_rows": 256,
        "scratch_root": str(tmp_path / "scratch"),
    }
    data = json.loads(recipe_path.read_text())
    data["large_dp"] = option
    recipe_path.write_text(json.dumps(data))
    recipe = read_recipe(recipe_path, expected_repo=repo)
    after = canonical_sources(recipe, plans)
    assert after["scientific_contracts"] == before["scientific_contracts"]
    assert after["per_release"] == before["per_release"]
    assert after["limits"] == before["limits"]
    assert discovery_fingerprint(after) != old_fingerprint
    assert discovery_fingerprint(before) == old_fingerprint
    assert str(linear) in node_dependencies(after, "alignment")
    assert str(linear) not in node_dependencies(after, "stage07")
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps(after))
    assert read_dag_spec(spec)["large_dp"] == option
    arguments = large_dp_arguments(after)
    assert arguments["linear_dp_binary"] == linear
    assert arguments["dp_scratch_root"] == tmp_path / "scratch"
    first, evidence = content_identity(recipe, {})
    assert "tool:linear_dp_binary" in evidence["direct_inputs_sha256"]
    linear.write_text("second exact engine\n")
    second, _ = content_identity(recipe, {})
    assert first != second
    changed = copy.deepcopy(after)
    changed["large_dp"]["checkpoint_rows"] = 128
    assert discovery_fingerprint(changed) != discovery_fingerprint(after)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("max_dp_memory_bytes", True),
        ("checkpoint_rows", 0),
        ("scratch_root", "/"),
        ("binary", "relative-program"),
        ("unsupported", 1),
    ],
)
def test_invalid_execution_configuration_fails_before_work(
    tmp_path: Path, key: str, value: object
) -> None:
    option = {
        "binary": str(tmp_path / "source/linear"),
        "max_dp_memory_bytes": 536_870_912,
        "checkpoint_rows": 256,
        "scratch_root": str(tmp_path / "scratch"),
    }
    option[key] = value
    with pytest.raises(ValueError):
        validate_large_dp(option)
