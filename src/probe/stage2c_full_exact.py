"""Pinned discovery plan for the independent FULL UniProt cross-strain scan.

No GO-donor policy belongs in this plan. All outputs are immutable.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

from probe.workflow.artifacts import StageManifest, validate_bundle

ROLES = ("START", "END")
SCHEMA = "probe-stage2c-full-exact-plan-v1"
SCIENCE = "same-species-distinct-taxid-complete-sequence-exact-v1"
CODE_FILES = (
    "cpp/src/probe_full_exact_cross_strain_v1.cpp",
    "src/probe/stage2c_full_exact.py",
    "workflow/scripts/run_stage_03_full_exact.py",
)
BINARY = ".pixi/build/probe-full-exact-cross-strain/probe-full-exact-cross-strain-v1"
INPUT_STAGES = {
    "targets": "01",
    "full": "01A_FULL",
    "taxonomy": "02B",
    "ox": "04D0b",
}
OUTPUT_BASENAME = "03_full_exact_cross_strain"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file(path: Path) -> Path:
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"Not a regular input file: {path}")
    return resolved


def _code_file(path: Path) -> Path:
    if path.is_symlink():
        raise ValueError(f"Code must not be a symlink: {path}")
    return _file(path)


def _source_paths(run: Path, role: str) -> dict[str, Path]:
    root = run / "work/pipeline-v2"
    return {
        "targets": root / "common/01/manifest.json",
        "full": root / role / "01A_FULL/manifest.json",
        "taxonomy": root / role / "02B/manifest.json",
        "ox": root / role / "04D0b/manifest.json",
    }


def _sources(run: Path, role: str) -> dict:
    paths = _source_paths(run, role)
    manifests: dict[str, StageManifest] = {}
    result = {}
    for label, path in paths.items():
        real = _file(path)
        manifest = StageManifest.read(real)
        if manifest.stage != INPUT_STAGES[label]:
            raise ValueError(f"Unexpected {label} upstream stage")
        if manifest.role != (None if label == "targets" else role):
            raise ValueError(f"Unexpected {label} upstream role")
        manifests[label] = manifest
        result[label] = {
            "path": str(path),
            "resolved": str(real),
            "sha256": sha256(real),
        }
    releases = {manifests[name].release_id for name in ("full", "taxonomy", "ox")}
    if len(releases) != 1 or None in releases:
        raise ValueError("FULL exact upstream release mismatch")
    required = {
        "targets": "01_targets.tsv",
        "full": "01A_full_v2.fasta",
        "taxonomy": "02B_target_taxonomy.tsv",
        "ox": "full_ox_taxonomy.tsv",
    }
    for label, filename in required.items():
        declared = {item.path for item in manifests[label].outputs}
        if filename not in declared:
            raise ValueError(f"Upstream lacks declared output: {filename}")
    result["release_id"] = releases.pop()
    return result


def _key(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_plan(repo: Path, run: Path) -> dict:
    repo = repo.resolve(strict=True)
    run = run.resolve(strict=True)
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--is-inside-work-tree"],
        check=True,
        capture_output=True,
        text=True,
    )
    if result.stdout.strip() != "true":
        raise ValueError("Not a Git worktree")
    code = {name: sha256(_code_file(repo / name)) for name in CODE_FILES}
    binary_path = _code_file(repo / BINARY)
    sources = {role: _sources(run, role) for role in ROLES}
    inputs = {
        "schema": SCHEMA,
        "science": SCIENCE,
        "sources": sources,
        "code": code,
        "binary_sha256": sha256(binary_path),
        "parameters": {
            "scope": "same_species_distinct_resolved_taxid",
            "identity": "full_sequence_literal_exact",
            "fragment": "exclude",
            "residue_policy": "unambiguous_only",
        },
    }
    key = _key(inputs)
    paths = {
        role: str(run / "work/pipeline-v2" / role / "03_full_exact_variants" / key)
        for role in ROLES
    }
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return {
        **inputs,
        "key": key,
        "paths": paths,
        "repo": str(repo),
        "run": str(run),
        "binary": str(binary_path),
        "git_head_at_creation": head,
    }


def verify_plan(plan: dict) -> None:
    if plan.get("schema") != SCHEMA or plan.get("science") != SCIENCE:
        raise ValueError("Unsupported FULL exact plan schema")
    current = build_plan(Path(plan["repo"]), Path(plan["run"]))
    for name in (
        "schema",
        "science",
        "sources",
        "code",
        "binary_sha256",
        "parameters",
        "key",
        "paths",
        "repo",
        "run",
        "binary",
    ):
        if plan.get(name) != current[name]:
            raise ValueError(f"FULL exact pinned plan changed: {name}")
    run = Path(plan["run"])
    for role in ROLES:
        output = Path(plan["paths"][role])
        parent = run / "work/pipeline-v2" / role / "03_full_exact_variants"
        if output.parent != parent or output.name != plan["key"]:
            raise ValueError("Unsafe FULL exact output destination")
        cursor = output
        while cursor != run:
            if cursor.is_symlink():
                raise ValueError("Symlink in FULL exact publication path")
            cursor = cursor.parent
        if not output.exists():
            continue
        if not output.is_dir():
            raise ValueError("FULL exact output is not a directory")
        if not any(output.iterdir()):
            continue
        if not (output / "manifest.json").is_file():
            raise ValueError("Nonempty unpublished FULL exact directory")
        published = validate_bundle(output, deep=False)
        if (
            published.stage != "03_FULL_EXACT"
            or published.role != role
            or published.implementation.get("discovery_fingerprint") != plan["key"]
        ):
            raise ValueError("Incompatible FULL exact publication")


def read_plan(path: Path) -> dict:
    obj = json.loads(_file(path).read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError("FULL exact plan must be a mapping")
    verify_plan(obj)
    return obj


def create_plan(repo: Path, run: Path) -> Path:
    plan = build_plan(repo, run)
    run_root = Path(plan["run"])
    output = (
        run_root
        / "experiments"
        / f"full-exact-discovery-{plan['key'][:16]}"
        / "discovery.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(plan, indent=2, sort_keys=True) + "\n"
    if output.exists() or output.is_symlink():
        old = read_plan(output)
        if old["key"] != plan["key"]:
            raise FileExistsError("Existing FULL exact plan is incompatible")
        return output
    descriptor, name = tempfile.mkstemp(
        prefix=".full-exact-", suffix=".json", dir=output.parent
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return output
