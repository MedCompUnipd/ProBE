"""Pure synthetic fingerprints and safety guards for resolver variants."""

import copy
import json
from pathlib import Path

import pytest

from probe.stage07_variants import (
    CODE_GROUPS,
    SCHEMA,
    _keys,
    read_spec,
    sha256,
)


def policy() -> dict:
    return {
        "resolver": {"subtaxon_policy": "strict"},
        "annotation_donor": {
            "partial_sequence": "report_only",
            "documentary_fragment": "allow",
        },
    }


def base() -> dict:
    keys = [key for group in CODE_GROUPS.values() for key in group]
    return {
        "code_hashes": {key: "a" * 64 for key in keys},
        "source_hashes": {
            "primary": "b" * 64,
            "canonical_stage07": "c" * 64,
            "start_goa": "d" * 64,
            "end_goa": "e" * 64,
            "go_policy": "f" * 64,
        },
        "ontology_sha256": "0" * 64,
        "registry_sha256": "1" * 64,
        "evidence_policy_sha256": "2" * 64,
    }


def test_evidence_changes_reuse_only_stage07_m8_m10c() -> None:
    data = base()
    first = _keys(policy(), data)
    changed = copy.deepcopy(data)
    changed["evidence_policy_sha256"] = "9" * 64
    later = _keys(policy(), changed)
    for key in ("stage07", "stage08", "m10c"):
        assert first[key] == later[key]
    assert first["benchmark"] != later["benchmark"]
    assert first["diagnostics"] != later["diagnostics"]


def test_resolver_change_invalidates_every_descendant() -> None:
    original = _keys(policy(), base())
    changed = policy()
    changed["annotation_donor"]["documentary_fragment"] = "report_only"
    later = _keys(changed, base())
    assert all(original[key] != later[key] for key in original)


def test_upstream_change_invalidates_every_descendant() -> None:
    first = _keys(policy(), base())
    changed = base()
    changed["source_hashes"]["primary"] = "8" * 64
    later = _keys(policy(), changed)
    assert all(first[key] != later[key] for key in first)


def test_goa_change_preserves_stage07_but_changes_m8_downstream() -> None:
    first = _keys(policy(), base())
    changed = base()
    changed["source_hashes"]["end_goa"] = "8" * 64
    later = _keys(policy(), changed)
    assert first["stage07"] == later["stage07"]
    assert all(first[k] != later[k] for k in first if k != "stage07")


def test_read_spec_blocks_illegal_destination(tmp_path: Path) -> None:
    run = tmp_path / "run"
    exp = run / "experiments" / "test-alpha"
    exp.mkdir(parents=True)
    document = {
        "schema_version": SCHEMA,
        "run": str(run),
        "experiment_id": "test-alpha",
        "policy": policy(),
        **base(),
    }
    document["keys"] = _keys(policy(), document)
    document["paths"] = {
        k: str(tmp_path / "outside" / v) for k, v in document["keys"].items()
    }
    file = exp / "config.resolved.json"
    file.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="Illegal resolver output"):
        read_spec(file, check_sources=False)


def test_read_spec_rejects_symlink(tmp_path: Path) -> None:
    original = tmp_path / "original.json"
    original.write_text("{}", encoding="utf-8")
    linked = tmp_path / "alias.json"
    linked.symlink_to(original)
    with pytest.raises(ValueError, match="physical regular"):
        read_spec(linked, check_sources=False)


def test_sha256_known_bytes(tmp_path: Path) -> None:
    target = tmp_path / "small"
    target.write_bytes(b"abc")
    assert sha256(target).startswith("ba7816bf8f01cfea")


def test_snakemake_plan_limits_dag_to_variant_rules(monkeypatch) -> None:
    import importlib.util

    script = (
        Path(__file__).resolve().parents[1]
        / "scripts/pipeline/07_manage_resolver_variants.py"
    )
    loaded = importlib.util.spec_from_file_location("manager_2b", script)
    assert loaded is not None and loaded.loader is not None
    module = importlib.util.module_from_spec(loaded)
    loaded.loader.exec_module(module)
    monkeypatch.setattr(module.shutil, "which", lambda _: "/bin/pixi")
    spec = {
        "repo": "/repo",
        "run": "/run",
        "experiment_id": "test-alpha",
        "paths": {"stage07": "/run/results/test-stage07"},
    }
    command = module.snakemake_args(spec, full=True, execute=False)
    assert "--allowed-rules" in command
    assert "--dry-run" in command
    assert "stage_08_resolver_diagnostics" in command
    assert "stage_07" not in command
    assert "stage_08" not in command


def test_full_execution_requires_authorization(tmp_path: Path) -> None:
    import importlib.util

    script = (
        Path(__file__).resolve().parents[1]
        / "scripts/pipeline/07_manage_resolver_variants.py"
    )
    loaded = importlib.util.spec_from_file_location("manager_gate", script)
    assert loaded is not None and loaded.loader is not None
    module = importlib.util.module_from_spec(loaded)
    loaded.loader.exec_module(module)
    spec = {"run": str(tmp_path), "experiment_id": "test-alpha"}
    with pytest.raises(ValueError, match="authorize-goa-scan"):
        module._run(spec, full=True, execute=True, authorized=False)
