"""Immutable union plan for two independent Stage 2C discovery branches.

This plan is independent of any Stage 07 donor policy. It uses existing
published scanner manifests, without computing sequence search in the planner.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from probe.stage2c_discovery import read_plan as read_species_plan
from probe.stage2c_full_exact import read_plan as read_full_plan
from probe.workflow.artifacts import StageManifest, validate_bundle

SCHEMA = "probe-stage2c-union-plan-v3"
SCIENCE = (
    "species-wide-go-positive-plus-full-literal-exact-all-taxids-with-fragments-v3"
)
ROLES = ("START", "END")
CODE_FILES = (
    "src/probe/stage2c_candidate_union.py",
    "src/probe/stage2c_union_plan.py",
    "scripts/pipeline/03_manage_discovery_union.py",
    "workflow/scripts/run_stage_03_discovery_union.py",
)
PAIRS = "03_unified_discovery.tsv"
SUMMARY = "03_unified_discovery.summary.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _key(payload: dict) -> str:
    value = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _physical_code(path: Path) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Missing or symlinked source file: {path}")
    return path.resolve(strict=True)


def _safe_input(path: Path) -> Path:
    actual = path.resolve(strict=True)
    if not actual.is_file():
        raise ValueError(f"Not a regular input file: {path}")
    return actual


def build_plan(repo: Path, species_path: Path, full_path: Path) -> dict:
    repo = repo.resolve(strict=True)
    species_path = _safe_input(species_path)
    full_path = _safe_input(full_path)
    if species_path == full_path:
        raise ValueError("The two discovery plans must be independent")
    species = read_species_plan(species_path)
    full = read_full_plan(full_path)
    if species["repo"] != str(repo) or full["repo"] != str(repo):
        raise ValueError("Discovery plans refer to different repositories")
    if species["run"] != full["run"]:
        raise ValueError("Discovery plans refer to different runs")
    run = Path(species["run"])
    code = {name: sha256(_physical_code(repo / name)) for name in CODE_FILES}
    taxonomy = {}
    releases = {}
    for role in ROLES:
        left = species["sources"][role]["taxonomy"]
        right = full["sources"][role]["taxonomy"]
        if (left["resolved"], left["sha256"]) != (right["resolved"], right["sha256"]):
            raise ValueError(f"Stage 2C taxonomy mismatch: {role}")
        manifest = StageManifest.read(_safe_input(Path(left["path"])))
        if manifest.stage != "02B" or manifest.role != role:
            raise ValueError(f"Invalid Stage 2C taxonomy manifest: {role}")
        release = full["sources"][role]["release_id"]
        if release != manifest.release_id:
            raise ValueError(f"Stage 2C release mismatch: {role}")
        taxonomy[role] = left
        releases[role] = release
    identity = {
        "schema": SCHEMA,
        "science": SCIENCE,
        "species_key": species["key"],
        "full_key": full["key"],
        "taxonomy": taxonomy,
        "releases": releases,
        "code": code,
        "parameters": {"allow_literal_fragments": True},
    }
    key = _key(identity)
    paths = {
        role: str(run / "work/pipeline-v2" / role / "03_discovery_union_variants" / key)
        for role in ROLES
    }
    return {
        **identity,
        "key": key,
        "repo": str(repo),
        "run": str(run),
        "species_plan": str(species_path),
        "full_plan": str(full_path),
        "species_paths": species["paths"],
        "full_paths": full["paths"],
        "paths": paths,
    }


def _check_destination(spec: dict, role: str) -> None:
    run = Path(spec["run"])
    destination = Path(spec["paths"][role])
    expected = (
        run / "work/pipeline-v2" / role / "03_discovery_union_variants" / spec["key"]
    )
    if destination != expected:
        raise ValueError("Invalid Stage 2C union destination")
    current = destination
    while current != run:
        if current.is_symlink():
            raise ValueError("Symlinked union output path is forbidden")
        current = current.parent
    if not destination.exists():
        return
    if not destination.is_dir():
        raise FileExistsError(destination)
    if not any(destination.iterdir()):
        return
    if not (destination / "manifest.json").is_file():
        raise FileExistsError("Nonempty unpublished union destination")
    manifest = validate_bundle(destination, deep=False)
    if (
        manifest.stage != "03_UNION"
        or manifest.role != role
        or manifest.release_id != spec["releases"][role]
        or manifest.implementation.get("discovery_fingerprint") != spec["key"]
    ):
        raise ValueError("Incompatible existing union publication")


def verify_plan(spec: dict) -> None:
    if spec.get("schema") != SCHEMA or spec.get("science") != SCIENCE:
        raise ValueError("Unknown Stage 2C union plan schema")
    rebuilt = build_plan(
        Path(spec["repo"]),
        Path(spec["species_plan"]),
        Path(spec["full_plan"]),
    )
    if rebuilt != spec:
        raise ValueError("Pinned Stage 2C union plan changed")
    for role in ROLES:
        _check_destination(spec, role)


def read_plan(path: Path) -> dict:
    path = _safe_input(path)
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError("Union plan must be an object")
    verify_plan(result)
    return result


def create_plan(repo: Path, species_path: Path, full_path: Path) -> Path:
    spec = build_plan(repo, species_path, full_path)
    destination = (
        Path(spec["run"])
        / "experiments"
        / f"discovery-union-{spec['key'][:16]}"
        / "discovery.json"
    )
    payload = json.dumps(spec, indent=2, sort_keys=True) + "\n"
    if destination.exists() or destination.is_symlink():
        old = read_plan(destination)
        if old != spec:
            raise FileExistsError("Existing union plan differs")
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".union-", dir=destination.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination
