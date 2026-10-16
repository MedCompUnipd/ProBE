"""Pinned, immutable opt-in integration plan for Stage 2C evidence variants."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from probe.stage2c_union_plan import read_plan as read_union_plan

SCHEMA = "probe-stage2c-evidence-dag-plan-v1"
REVISION_SCHEMA = "probe-stage2c-evidence-dag-plan-v2"
ROLES = ("START", "END")
POLICIES = ("strict", "same_species_exact")
PHASES = (
    "delta",
    "bridge",
    "reuse",
    "full_recovery",
    "reconcile",
    "projection",
    "coverage",
    "go_recovery",
)
INHERITED_PHASES = (
    "delta",
    "bridge",
    "reuse",
    "full_recovery",
    "reconcile",
)
SUMMARY_NAMES = {
    "delta": "2C_candidate_delta.summary.json",
    "bridge": "2C_evidence_bridge.summary.json",
    "reuse": "2C_full_metadata_reuse.summary.json",
    "full_recovery": "2C_pending_full_recovery.summary.json",
    "reconcile": "2C_all_full_metadata.summary.json",
    "projection": "2C_05A2_projection.summary.json",
    "coverage": "2C_05D_sequence_coverage.summary.json",
    "go_recovery": "2C_go_positive_sequence_recovery.summary.json",
    "preview": "2C_alignment_preview.summary.json",
    "completion": "2C_alignment_completion.summary.json",
    "resolver": "2C_07_summary.json",
}


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def source_files(repo: Path) -> list[Path]:
    files = [
        *sorted((repo / "src/probe").glob("stage2c_*.py")),
        *sorted((repo / "src/probe").glob("resolver*.py")),
        *sorted((repo / "scripts/pipeline").glob("05_*stage2c*.py")),
        *sorted((repo / "scripts/pipeline").glob("05_*metadata*.py")),
        *sorted((repo / "scripts/pipeline").glob("05_*candidate*.py")),
        *sorted((repo / "scripts/pipeline").glob("06_*stage2c*.py")),
        repo / "scripts/pipeline/07_resolve_stage2c_variant.py",
        repo / "scripts/pipeline/05_recover_pending_go_sequences.py",
        repo / "scripts/pipeline/06A_prepare_alignment_jobs.py",
        repo / "scripts/pipeline/NONEXACT_select_alignment_jobs.py",
        repo / "scripts/pipeline/06C1_finalize_directional_glocal.py",
        repo / "cpp/src/probe_directional_glocal_full_dp_v1.cpp",
        repo / "cpp/CMakeLists.txt",
        repo / "pixi.toml",
        repo / "pixi.lock",
        repo / "workflow/Snakefile",
        repo / "workflow/stage2c_variants.smk",
        repo / "workflow/scripts/run_stage_2c_variant.py",
        repo / "src/probe/workflow/artifacts.py",
    ]
    selected = sorted(set(files))
    for path in selected:
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Unpinned or symlinked integration source: {path}")
    if len(selected) < 25:
        raise ValueError("Incomplete Stage 2C integration source universe")
    return selected


def _legacy_plan_identity(plan: dict) -> dict:
    """Reconstruct the immutable V1 identity without executing legacy code."""
    return {
        name: plan[name]
        for name in (
            "schema",
            "union_key",
            "union_spec",
            "releases",
            "canonical_manifests",
            "source_sha256",
        )
    }


def _legacy_parent(path: Path, repo: Path, union: dict) -> dict:
    """Verify V1 provenance and immutable published payloads for inheritance.

    V1 source hashes are historical facts. Recomputing them against revised
    projection code would incorrectly invalidate already published evidence.
    """
    if path.is_symlink():
        raise ValueError("Symlinked parent evidence plan")
    path = path.resolve(strict=True)
    plan = json.loads(path.read_text(encoding="utf-8"))
    if plan.get("schema") != SCHEMA:
        raise ValueError("The inherited evidence plan must be V1")
    if (
        plan.get("repo") != str(repo)
        or plan.get("run") != union["run"]
        or plan.get("union_spec") != str(Path(union["spec"]).resolve())
        or plan.get("union_key") != union["key"]
        or plan.get("releases") != union["releases"]
    ):
        raise ValueError("Inherited evidence plan identity mismatch")
    identity = _legacy_plan_identity(plan)
    payload = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    key = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    if plan.get("key") != key:
        raise ValueError("Inherited evidence plan key mismatch")
    run = Path(plan["run"])
    parent_root = run / "work/pipeline-v2/stage2c_evidence_variants" / key
    if plan.get("root") != str(parent_root):
        raise ValueError("Inherited evidence root mismatch")
    for name, expected in plan["canonical_manifests"].items():
        canonical = run / name
        if canonical.is_symlink() or sha(canonical) != expected:
            raise ValueError("Inherited canonical manifest changed")
    for role in ROLES:
        for phase in PHASES:
            expected = str(parent_root / role / phase)
            if plan["paths"][role][phase] != expected:
                raise ValueError("Inherited evidence path mismatch")
    return plan


def _inherited_receipts(parent: dict) -> dict:
    """Pin SHA256 for all successful upstream ready receipts and outputs."""
    pins = {}
    root = Path(parent["root"])
    for role in ROLES:
        pins[role] = {}
        for phase in INHERITED_PHASES:
            dest = root / role / phase
            stamp = root / role / f"{phase}.ready.json"
            if dest.is_symlink() or stamp.is_symlink():
                raise ValueError("Symlinked inherited publication")
            receipt = json.loads(stamp.read_text(encoding="utf-8"))
            summary = dest / SUMMARY_NAMES[phase]
            files = receipt.get("publication_sha256", {})
            if (
                receipt.get("schema") != SCHEMA
                or receipt.get("integration_key") != parent["key"]
                or receipt.get("role") != role
                or receipt.get("phase") != phase
                or receipt.get("summary") != str(summary)
                or receipt.get("summary_sha256") != sha(summary)
                or not isinstance(files, dict)
                or len(files) < 2
                or files.get(summary.name) != sha(summary)
            ):
                raise ValueError("Invalid inherited Stage 2C receipt")
            contents = json.loads(summary.read_text(encoding="utf-8"))
            payload_hashes = contents.get("output_sha256")
            if payload_hashes is not None:
                if not isinstance(payload_hashes, dict):
                    raise ValueError("Invalid inherited summary checksums")
                if set(files) != set(payload_hashes) | {summary.name}:
                    raise ValueError("Inherited publication file universe mismatch")
            if {p.name for p in dest.iterdir()} != set(files):
                raise ValueError("Unexpected file in inherited publication")
            for name, digest in files.items():
                file = dest / name
                if file.is_symlink() or not file.is_file() or sha(file) != digest:
                    raise ValueError("Inherited payload checksum mismatch")
                if (
                    payload_hashes is not None
                    and name != summary.name
                    and payload_hashes.get(name) != digest
                ):
                    raise ValueError("Inherited summary checksum mismatch")
            pins[role][phase] = sha(stamp)
    return pins


def build_plan(
    repo: Path,
    union_spec: Path,
    parent_spec: Path | None = None,
) -> dict:
    repo = repo.resolve(strict=True)
    union_spec = union_spec.resolve(strict=True)
    union = read_union_plan(union_spec)
    if union["repo"] != str(repo):
        raise ValueError("Stage 2C union belongs to a different repository")
    run = Path(union["run"])
    if not run.is_dir() or run.is_symlink():
        raise ValueError("Unexpected Stage 2C run directory")
    # The union reader still validates its separate pinned scanner sources.
    union = {**union, "spec": str(union_spec)}
    source_hashes = {
        str(path.relative_to(repo)): sha(path) for path in source_files(repo)
    }
    canonical = [
        "work/pipeline-v2/common/01/manifest.json",
        "work/pipeline-v2/common/04D1/manifest.json",
        "results/pipeline-v2/common/05A2/manifest.json",
        "results/pipeline-v2/common/05C2/manifest.json",
        "results/pipeline-v2/common/06C1/manifest.json",
    ]
    for role in ROLES:
        for stage in ("01A_FULL", "01C", "02B", "04B", "04D2", "05D"):
            canonical.append(f"work/pipeline-v2/{role}/{stage}/manifest.json")
    manifests = {}
    for name in canonical:
        file = run / name
        if not file.is_file() or file.is_symlink():
            raise ValueError(f"Missing or symlinked canonical manifest: {file}")
        manifests[name] = sha(file)
    inheritance = None
    if parent_spec is not None:
        parent = _legacy_parent(parent_spec, repo, union)
        inheritance = {
            "parent_plan": str(parent_spec.resolve(strict=True)),
            "parent_plan_sha256": sha(parent_spec),
            "parent_key": parent["key"],
            "receipt_sha256": _inherited_receipts(parent),
        }
    identity = {
        "schema": REVISION_SCHEMA if inheritance is not None else SCHEMA,
        "union_key": union["key"],
        "union_spec": str(union_spec),
        "releases": union["releases"],
        "canonical_manifests": manifests,
        "source_sha256": source_hashes,
    }
    if inheritance is not None:
        identity["inheritance"] = inheritance
    payload = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    key = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    root = run / "work/pipeline-v2/stage2c_evidence_variants" / key
    paths = {
        role: {phase: str(root / role / phase) for phase in PHASES} for role in ROLES
    }
    if inheritance is not None:
        parent_root = Path(parent["root"])
        for role in ROLES:
            for phase in INHERITED_PHASES:
                paths[role][phase] = str(parent_root / role / phase)
    paths["common"] = {
        "preview": str(root / "common/preview"),
        "completion": str(root / "common/completion"),
    }
    paths["resolver"] = {policy: str(root / "resolver" / policy) for policy in POLICIES}
    return {
        **identity,
        "key": key,
        "repo": str(repo),
        "run": str(run),
        "root": str(root),
        "paths": paths,
        "policies": list(POLICIES),
    }


def read_plan(path: Path) -> dict:
    path = path.resolve(strict=True)
    plan = json.loads(path.read_text(encoding="utf-8"))
    inheritance = plan.get("inheritance")
    if inheritance is not None and plan.get("schema") != REVISION_SCHEMA:
        raise ValueError("Unexpected Stage 2C inheritance schema")
    parent_spec = Path(inheritance["parent_plan"]) if inheritance else None
    rebuilt = build_plan(Path(plan["repo"]), Path(plan["union_spec"]), parent_spec)
    if plan != rebuilt:
        raise ValueError("Stage 2C evidence plan sources/manifest changed")
    return rebuilt


def create_plan(repo: Path, union_spec: Path, parent_spec: Path | None = None) -> Path:
    plan = build_plan(repo, union_spec, parent_spec)
    base = Path(plan["run"]) / "experiments"
    directory = base / f"stage2c-evidence-{plan['key'][:16]}"
    destination = directory / "integration.json"
    for parent in (base, directory):
        if parent.is_symlink():
            raise ValueError("Symlinked experiment output path")
    if destination.is_symlink():
        raise ValueError("Symlinked integration plan is forbidden")
    data = json.dumps(plan, indent=2, sort_keys=True) + "\n"
    if destination.exists() or destination.is_symlink():
        read_plan(destination)
        return destination
    directory.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=directory, prefix=".integration-")
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(tmp, destination)
    finally:
        tmp.unlink(missing_ok=True)
    return destination
