"""A canonical DAG cannot accidentally activate science or mix releases."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from probe.canonical_v2.dag_contract import (
    COMMON_INPUTS,
    RELEASE_INPUTS,
    ROLES,
    SCIENTIFIC_CONTRACTS,
    TOOLS,
    VERSION,
    destination,
    discovery_fingerprint,
    policy_fingerprint,
    read_dag_spec,
)
from probe.canonical_v2.dag_execution import dispatch


def _spec(tmp_path: Path) -> dict:
    def source(label: str) -> str:
        return str(tmp_path / "sources" / label)

    return {
        "schema": VERSION,
        "scientific_contracts": dict(SCIENTIFIC_CONTRACTS),
        "mode": "prepare",
        "policy": "same_species_exact",
        "releases": {"START": "R1", "END": "R2"},
        "output_root": str(tmp_path / "outputs"),
        "inputs": {name: source(name) for name in COMMON_INPUTS},
        "per_release": {
            role: {name: source(f"{role}-{name}") for name in RELEASE_INPUTS}
            for role in ROLES
        },
        "tools": {name: source(f"tool-{name}") for name in TOOLS},
        "limits": {
            "threads": 1,
            "max_pair_cells": 10000,
            "max_total_cells": 50000,
            "max_chunk_cells": None,
            "partial_sequence": "allow",
            "documentary_fragment": "allow",
        },
    }


def _write(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_canonical_spec_and_local_release_directories(tmp_path: Path) -> None:
    loaded = read_dag_spec(_write(tmp_path, _spec(tmp_path)))
    assert destination(loaded, "exact", "START") != destination(loaded, "exact", "END")
    assert destination(loaded, "stage07").is_relative_to(Path(loaded["output_root"]))
    assert loaded["mode"] == "prepare"


@pytest.mark.parametrize("key", ["inputs", "tools", "per_release"])
def test_missing_inputs_fail_closed(tmp_path: Path, key: str) -> None:
    spec = _spec(tmp_path)
    spec[key].pop(next(iter(spec[key])))
    with pytest.raises(ValueError):
        read_dag_spec(_write(tmp_path, spec))


def test_output_path_cannot_be_source(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    spec["inputs"]["targets"] = spec["output_root"] + "/raw"
    with pytest.raises(ValueError, match="source lies inside"):
        read_dag_spec(_write(tmp_path, spec))


def test_one_policy_per_run(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    spec["policy"] = "mixed"
    with pytest.raises(ValueError, match="donor policy"):
        read_dag_spec(_write(tmp_path, spec))


def test_shared_release_id_is_forbidden(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    spec["releases"]["END"] = "R1"
    with pytest.raises(ValueError, match="release identity"):
        read_dag_spec(_write(tmp_path, spec))


def test_executable_dp_fails_when_mode_is_prepare(tmp_path: Path) -> None:
    spec = read_dag_spec(_write(tmp_path, _spec(tmp_path)))
    with pytest.raises(ValueError, match="mode=execute"):
        dispatch(
            spec=spec,
            stage="alignment",
            target=destination(spec, "alignment"),
        )
    assert not Path(spec["output_root"]).exists()


def test_unknown_dag_target_and_wrong_release(tmp_path: Path) -> None:
    spec = read_dag_spec(_write(tmp_path, _spec(tmp_path)))
    with pytest.raises(ValueError, match="Unknown canonical DAG stage"):
        destination(spec, "unknown")
    with pytest.raises(ValueError, match="role mismatch"):
        destination(spec, "stage07", "END")
    with pytest.raises(ValueError, match="Foreign canonical"):
        destination(spec, "exact", "R3")


def test_switching_donor_policy_reuses_discovery(tmp_path: Path) -> None:
    spec = read_dag_spec(_write(tmp_path, _spec(tmp_path)))
    extended = {**spec, "policy": "strict"}
    assert destination(spec, "workload") == destination(extended, "workload")
    assert destination(spec, "alignment") == destination(extended, "alignment")
    assert destination(spec, "stage07") != destination(extended, "stage07")
    assert destination(spec, "temporal") != destination(extended, "temporal")


@pytest.mark.parametrize("mode", ("prepare", "execute"))
def test_legacy_contract_is_not_silently_upgraded(
    tmp_path: Path,
    mode: str,
) -> None:
    spec = _spec(tmp_path)
    spec["schema"] = "probe-canonical-v2-dag-input-v1"
    spec["mode"] = mode
    with pytest.raises(ValueError, match="contract version"):
        read_dag_spec(_write(tmp_path, spec))
    assert not Path(spec["output_root"]).exists()


def test_explicit_scientific_contract_mapping_is_required(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    del spec["scientific_contracts"]
    with pytest.raises(ValueError, match="specification keys"):
        read_dag_spec(_write(tmp_path, spec))


@pytest.mark.parametrize("contract", tuple(SCIENTIFIC_CONTRACTS))
@pytest.mark.parametrize("change", ("missing", "different"))
def test_every_scientific_contract_is_strict(
    tmp_path: Path,
    contract: str,
    change: str,
) -> None:
    spec = _spec(tmp_path)
    if change == "missing":
        del spec["scientific_contracts"][contract]
    else:
        spec["scientific_contracts"][contract] = "unverified-other-contract"
    with pytest.raises(ValueError, match="scientific contracts"):
        read_dag_spec(_write(tmp_path, spec))


def test_unknown_scientific_contract_cannot_be_ignored(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    spec["scientific_contracts"]["silent_fallback"] = "full_reference_v1"
    with pytest.raises(ValueError, match="scientific contracts"):
        read_dag_spec(_write(tmp_path, spec))


@pytest.mark.parametrize("contract", tuple(SCIENTIFIC_CONTRACTS))
def test_each_scientific_contract_changes_both_fingerprints(
    tmp_path: Path,
    contract: str,
) -> None:
    spec = _spec(tmp_path)
    changed = {
        **spec,
        "scientific_contracts": {
            **spec["scientific_contracts"],
            contract: "different-science",
        },
    }
    assert discovery_fingerprint(spec) != discovery_fingerprint(changed)
    assert policy_fingerprint(spec) != policy_fingerprint(changed)
