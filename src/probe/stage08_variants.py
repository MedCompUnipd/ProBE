"""Fingerprint and protect evidence-only Stage 08 benchmark experiments.

Resolver changes are deliberately unsupported in this first increment.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

import yaml

SCHEMA = "stage08-evidence-variant-v1"
EDGES = "0,300,600,1023,1500,3000"


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def digest_object(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def regular(path: Path, allow_symlink: bool = False) -> Path:
    if (path.is_symlink() and not allow_symlink) or not path.is_file():
        raise ValueError(f"Expected physical regular file: {path}")
    return path.resolve(strict=True)


def _merge(a: dict, b: dict) -> dict:
    merged = dict(a)
    for key, value in b.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def check_edges(value: str) -> str:
    edges = [int(item) for item in value.split(",")]
    if len(edges) < 2 or edges[0] != 0:
        raise ValueError("Length edges must start with 0")
    if any(x >= y for x, y in zip(edges, edges[1:], strict=False)):
        raise ValueError("Length edges must increase")
    return ",".join(map(str, edges))


def create(
    repo: Path,
    run: Path,
    name: str,
    policy: Path,
    registry: Path,
    ontology: Path,
    workflow_config: Path,
    length_edges: str = EDGES,
    min_length: int | None = None,
    max_length: int | None = None,
    taxids: str | None = None,
) -> dict:
    """Snapshot exact inputs; never overwrite existing experiment outputs."""
    repo = repo.resolve(strict=True)
    run = run.resolve(strict=True)
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,38}[a-z0-9]", name):
        raise ValueError("Name requires 3..40 lowercase alphanumeric/hyphen")
    if min_length is not None and min_length < 1:
        raise ValueError("min_length must be positive")
    if max_length is not None and max_length < 1:
        raise ValueError("max_length must be positive")
    if min_length is not None and max_length is not None and min_length > max_length:
        raise ValueError("min_length > max_length")
    length_edges = check_edges(length_edges)
    if taxids is not None:
        parts = taxids.split(",")
        if any(not v.isdecimal() or int(v) < 1 for v in parts):
            raise ValueError("TaxIDs must be positive comma-separated integers")
        taxids = ",".join(sorted(set(parts), key=int))
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
    )
    if status.stdout.strip():
        raise ValueError("Commit repository changes before creating variants")
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    common = run / "results/pipeline-v2/common"
    source_paths = {
        "m10c_manifest": common / "08_interpret_compact/manifest.json",
        "m8_summary": common / "08/08_collection_summary.json",
        "targets": run / "inputs/targets.tsv",
        "stage01": run / "work/pipeline-v2/common/01/01_targets.tsv",
        "ontology": ontology,
    }
    sources = {
        k: str(regular(p, allow_symlink=k == "targets"))
        for k, p in source_paths.items()
    }
    hashes = {k: digest_file(Path(p)) for k, p in sources.items()}
    m10c = read_json(Path(sources["m10c_manifest"]))
    m8 = read_json(Path(sources["m8_summary"]))
    if hashes["ontology"] != m10c["ontology_sha256"]:
        raise ValueError("Ontology SHA256 mismatch with M10c")
    releases = {role: m8["by_role"][role]["release"] for role in ("START", "END")}
    base = yaml.safe_load((repo / "config/pipeline.yaml").read_text())
    override = yaml.safe_load(regular(workflow_config).read_text())
    if not isinstance(base, dict) or not isinstance(override, dict):
        raise ValueError("Workflow configs must be YAML mappings")
    resolved = _merge(base, override)
    for role in ("START", "END"):
        if resolved["releases"][role]["release_id"] != releases[role]:
            raise ValueError(f"Workflow {role} differs from M8 release")
    policy = regular(policy)
    registry = regular(registry)
    from probe.stage08_temporal_v2 import load_classes

    load_classes(registry, policy)
    repo_files = {
        "benchmark_cli": repo / "scripts/pipeline/08_build_benchmark_v2.py",
        "benchmark_module": repo / "src/probe/stage08_temporal_v2.py",
        "diagnostics_cli": repo / "scripts/pipeline/08_profile_benchmark.py",
        "variant_module": repo / "src/probe/stage08_variants.py",
        "temporal_base": repo / "src/probe/stage08_temporal_benchmark.py",
        "registry_module": repo / "src/probe/stage08_evidence_registry.py",
    }
    code_hashes = {k: digest_file(regular(p)) for k, p in repo_files.items()}
    policy_sha = digest_file(policy)
    registry_sha = digest_file(registry)
    benchmark_code = (
        "benchmark_cli",
        "benchmark_module",
        "temporal_base",
        "registry_module",
    )
    key = digest_object(
        {
            "sources": hashes,
            "policy": policy_sha,
            "registry": registry_sha,
            "releases": releases,
            "code": [code_hashes[name] for name in benchmark_code],
        }
    )
    filters = {
        "length_edges": length_edges,
        "min_length": min_length,
        "max_length": max_length,
        "taxids": taxids,
    }
    stats_key = digest_object(
        {
            "benchmark": key,
            "filters": filters,
            "code": code_hashes["diagnostics_cli"],
        }
    )
    eid = f"{name}-{key[:12]}"
    exp = run / "experiments" / eid
    benchmark = common / "08_benchmark_variants" / key
    statistics = common / "08_diagnostics_variants" / stats_key
    if exp.exists() or exp.is_symlink():
        raise FileExistsError(f"Experiment already exists: {eid}")
    spec = {
        "schema_version": SCHEMA,
        "experiment_id": eid,
        "run": str(run),
        "repo": str(repo),
        "git_commit": head,
        "sources": sources,
        "source_hashes": hashes,
        "code_hashes": code_hashes,
        "policy_sha256": policy_sha,
        "registry_sha256": registry_sha,
        "releases": releases,
        "pipeline_config": resolved,
        "policy": str(exp / "policy.json"),
        "registry": str(exp / "registry.json"),
        "benchmark_key": key,
        "diagnostic_key": stats_key,
        "benchmark": str(benchmark),
        "statistics": str(statistics),
        "filters": filters,
    }
    exp.parent.mkdir(exist_ok=True)
    benchmark.parent.mkdir(exist_ok=True)
    statistics.parent.mkdir(exist_ok=True)
    stage = exp.parent / f".new-{uuid.uuid4().hex}"
    stage.mkdir()
    try:
        shutil.copyfile(policy, stage / "policy.json")
        shutil.copyfile(registry, stage / "registry.json")
        if digest_file(stage / "policy.json") != policy_sha:
            raise ValueError("Policy changed during snapshot")
        if digest_file(stage / "registry.json") != registry_sha:
            raise ValueError("Registry changed during snapshot")
        (stage / "config.resolved.json").write_text(
            json.dumps(spec, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        (stage / "experiment.yaml").write_text(
            yaml.safe_dump(
                {"benchmark_variant_spec": str(exp / "config.resolved.json")}
            ),
            encoding="utf-8",
        )
        (stage / "PARAMETERS.md").write_text(
            f"# {eid}\n\n"
            f"Git commit: `{head}`\n\n"
            f"Evidence policy SHA256: `{policy_sha}`\n\n"
            f"Benchmark artifact: `{benchmark}`\n\n"
            f"Statistics artifact: `{statistics}`\n\n"
            "Resolved settings: `config.resolved.json`\n\n"
            "Stage 07 and upstream M8/M10c are reused unchanged.\n",
            encoding="utf-8",
        )
        if exp.exists() or exp.is_symlink():
            raise FileExistsError("Concurrent experiment creation")
        os.rename(stage, exp)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return spec


def read_spec(path: Path, check_sources: bool = True) -> dict:
    path = regular(path)
    spec = read_json(path)
    if spec.get("schema_version") != SCHEMA:
        raise ValueError("Unknown experiment schema")
    run = Path(spec["run"]).resolve(strict=True)
    exp = run / "experiments" / spec["experiment_id"]
    if path != exp / "config.resolved.json":
        raise ValueError("Experiment specification in unexpected directory")
    if Path(spec["policy"]) != exp / "policy.json":
        raise ValueError("Policy path mismatch")
    if Path(spec["registry"]) != exp / "registry.json":
        raise ValueError("Registry path mismatch")
    common = run / "results/pipeline-v2/common"
    if Path(spec["benchmark"]) != (
        common / "08_benchmark_variants" / spec["benchmark_key"]
    ):
        raise ValueError("Benchmark destination mismatch")
    if Path(spec["statistics"]) != (
        common / "08_diagnostics_variants" / spec["diagnostic_key"]
    ):
        raise ValueError("Statistics destination mismatch")
    if digest_file(regular(Path(spec["policy"]))) != spec["policy_sha256"]:
        raise ValueError("Snapshotted policy was changed")
    if digest_file(regular(Path(spec["registry"]))) != spec["registry_sha256"]:
        raise ValueError("Snapshotted registry was changed")
    if check_sources:
        for key, expected in spec["source_hashes"].items():
            current = digest_file(regular(Path(spec["sources"][key])))
            if current != expected:
                raise ValueError(f"Variant source changed: {key}")
        repo = Path(spec["repo"])
        files = {
            "benchmark_cli": repo / "scripts/pipeline/08_build_benchmark_v2.py",
            "benchmark_module": repo / "src/probe/stage08_temporal_v2.py",
            "diagnostics_cli": repo / "scripts/pipeline/08_profile_benchmark.py",
            "variant_module": repo / "src/probe/stage08_variants.py",
            "temporal_base": repo / "src/probe/stage08_temporal_benchmark.py",
            "registry_module": repo / "src/probe/stage08_evidence_registry.py",
        }
        for key, expected in spec["code_hashes"].items():
            if digest_file(regular(files[key])) != expected:
                raise ValueError(f"Variant code changed: {key}")
    return spec


def list_variants(run: Path) -> list[dict]:
    root = run / "experiments"
    if not root.is_dir():
        return []
    rows = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or entry.is_symlink():
            continue
        spec_file = entry / "config.resolved.json"
        if not spec_file.is_file() or spec_file.is_symlink():
            continue
        spec = read_spec(spec_file, check_sources=False)
        rows.append(
            {
                "experiment_id": spec["experiment_id"],
                "benchmark_key": spec["benchmark_key"],
                "diagnostic_key": spec["diagnostic_key"],
                "benchmark_published": (
                    Path(spec["benchmark"]) / "manifest.json"
                ).is_file(),
                "statistics_published": (
                    Path(spec["statistics"]) / "publication_manifest.json"
                ).is_file(),
                "benchmark_path": spec["benchmark"],
                "statistics_path": spec["statistics"],
                "experiment_path": str(entry),
            }
        )
    return rows


def remove_variant(
    run: Path,
    eid: str,
    confirm: str | None = None,
    delete_unreferenced: bool = False,
) -> dict:
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,60}[a-z0-9]", eid):
        raise ValueError("Invalid experiment ID")
    run = run.resolve(strict=True)
    exp = run / "experiments" / eid
    spec = read_spec(exp / "config.resolved.json", check_sources=False)
    if spec["experiment_id"] != eid:
        raise ValueError("Experiment ID mismatch")
    if (exp / "RUNNING").exists():
        raise RuntimeError("Experiment marked as running")
    others = [r for r in list_variants(run) if r["experiment_id"] != eid]
    deletions = []
    if delete_unreferenced:
        for key, ref in (
            ("benchmark_key", "benchmark"),
            ("diagnostic_key", "statistics"),
        ):
            if not any(row[key] == spec[key] for row in others):
                deletions.append(Path(spec[ref]))
    preview = {
        "experiment_path": str(exp),
        "artifacts_to_delete": [str(p) for p in deletions],
        "dry_run": confirm is None,
    }
    if confirm is None:
        return preview
    if confirm != eid:
        raise ValueError("Confirmation must match experiment ID exactly")
    for path in deletions:
        if path.is_symlink():
            raise ValueError("Cannot delete symlink artifacts")
        if path.exists():
            if not path.is_dir():
                raise ValueError("Artifact is not a directory")
            shutil.rmtree(path)
    shutil.rmtree(exp)
    return preview
