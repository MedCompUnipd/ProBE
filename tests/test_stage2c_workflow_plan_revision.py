"""Verify immutable, hash-pinned V1-to-V2 Stage 2C evidence inheritance."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from probe import stage2c_workflow_plan as plans


def _json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _fake_upstream(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "source.py"
    source.write_text("print('changed source')\n", encoding="utf-8")
    run = tmp_path / "run"
    run.mkdir()
    spec = tmp_path / "union.json"
    _json(spec, {"placeholder": 1})
    union_key = "a" * 64
    releases = {"START": "2023_01", "END": "2026_03"}
    union = {
        "repo": str(repo),
        "run": str(run),
        "key": union_key,
        "releases": releases,
    }
    monkeypatch.setattr(plans, "read_union_plan", lambda path: union)
    monkeypatch.setattr(plans, "source_files", lambda path: [source])
    manifests = {}
    canonical = [
        "work/pipeline-v2/common/01/manifest.json",
        "work/pipeline-v2/common/04D1/manifest.json",
        "results/pipeline-v2/common/05A2/manifest.json",
        "results/pipeline-v2/common/05C2/manifest.json",
    ]
    for role in plans.ROLES:
        for stage in ("01A_FULL", "01C", "02B", "04B", "04D2", "05D"):
            canonical.append(f"work/pipeline-v2/{role}/{stage}/manifest.json")
    for name in canonical:
        path = run / name
        _json(path, {"stage": name})
        manifests[name] = plans.sha(path)
    # The immutable V1 parent predates the new 06C1 reuse dependency.
    _json(
        run / "results/pipeline-v2/common/06C1/manifest.json",
        {"stage": "06C1"},
    )
    old_identity = {
        "schema": plans.SCHEMA,
        "union_key": union_key,
        "union_spec": str(spec),
        "releases": releases,
        "canonical_manifests": manifests,
        "source_sha256": {"source.py": "0" * 64},
    }
    payload = json.dumps(old_identity, sort_keys=True, separators=(",", ":"))
    parent_key = hashlib.sha256(payload.encode()).hexdigest()
    parent_root = run / "work/pipeline-v2/stage2c_evidence_variants" / parent_key
    paths = {
        role: {phase: str(parent_root / role / phase) for phase in plans.PHASES}
        for role in plans.ROLES
    }
    parent = {
        **old_identity,
        "key": parent_key,
        "repo": str(repo),
        "run": str(run),
        "root": str(parent_root),
        "paths": paths,
    }
    parent_spec = run / "experiments" / "old" / "integration.json"
    _json(parent_spec, parent)
    for role in plans.ROLES:
        for phase in plans.INHERITED_PHASES:
            dest = parent_root / role / phase
            dest.mkdir(parents=True)
            summary = dest / plans.SUMMARY_NAMES[phase]
            output = dest / "payload.tsv"
            output.write_text(f"{role}-{phase}\n", encoding="utf-8")
            body = (
                {"output_sha256": {"payload.tsv": plans.sha(output)}}
                if phase == "reconcile"
                else {"schema": f"stage2c-{phase}-v1"}
            )
            _json(summary, body)
            receipt = {
                "schema": plans.SCHEMA,
                "integration_key": parent_key,
                "phase": phase,
                "role": role,
                "summary": str(summary),
                "summary_sha256": plans.sha(summary),
                "publication_sha256": {
                    "payload.tsv": plans.sha(output),
                    summary.name: plans.sha(summary),
                },
            }
            _json(parent_root / role / f"{phase}.ready.json", receipt)
    return repo, spec, parent_spec, parent_root


def test_revision_inherits_upstreams_without_republishing(tmp_path, monkeypatch):
    repo, union_spec, parent_spec, parent_root = _fake_upstream(tmp_path, monkeypatch)
    path = plans.create_plan(repo, union_spec, parent_spec)
    revision = plans.read_plan(path)
    assert revision["schema"] == plans.REVISION_SCHEMA
    assert (
        "results/pipeline-v2/common/06C1/manifest.json"
        in revision["canonical_manifests"]
    )
    assert revision["inheritance"]["parent_plan"] == str(parent_spec)
    assert revision["paths"]["END"]["reconcile"] == str(parent_root / "END/reconcile")
    assert revision["paths"]["START"]["projection"] == str(
        Path(revision["root"]) / "START/projection"
    )
    assert revision["key"] != parent_root.name
    assert plans.create_plan(repo, union_spec, parent_spec) == path


def test_revision_rejects_changed_upstream_receipt(tmp_path, monkeypatch):
    repo, union_spec, parent_spec, parent_root = _fake_upstream(tmp_path, monkeypatch)
    path = plans.create_plan(repo, union_spec, parent_spec)
    receipt = parent_root / "END/reconcile.ready.json"
    receipt.write_text(receipt.read_text() + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="sources/manifest changed"):
        plans.read_plan(path)


def test_revision_rejects_changed_upstream_payload(tmp_path, monkeypatch):
    repo, union_spec, parent_spec, parent_root = _fake_upstream(tmp_path, monkeypatch)
    path = plans.create_plan(repo, union_spec, parent_spec)
    payload = parent_root / "END/reconcile/payload.tsv"
    payload.write_text("TAMPERED\n", encoding="utf-8")
    with pytest.raises(ValueError, match="payload checksum mismatch"):
        plans.read_plan(path)


def test_revision_pins_canonical_06c1_reuse_manifest(
    tmp_path: Path, monkeypatch
) -> None:
    repo, union_spec, parent_spec, _ = _fake_upstream(tmp_path, monkeypatch)
    plan_path = plans.create_plan(repo, union_spec, parent_spec)
    manifest = tmp_path / "run/results/pipeline-v2/common/06C1/manifest.json"
    manifest.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="sources/manifest changed"):
        plans.read_plan(plan_path)
