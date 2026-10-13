"""Pinned, immutable Stage 2C species-wide Stage 03 discovery plans.

This creates *metadata only*. The full-V2 exact channel and downstream
candidate union are separate future stages and are not implied here.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import yaml

from probe.workflow.artifacts import StageManifest, validate_bundle

SCHEMA = "probe-species-wide-stage03-plan-v1"
SCIENCE = "species-wide-primary-go-positive-v1"
ROLES = ("START", "END")
CODE_FILES = (
    "cpp/src/probe_primary_candidate_scan_v2.cpp",
    "src/probe/workflow/candidate_scan.py",
    "src/probe/stage2c_discovery.py",
    "workflow/scripts/run_stage_03_species_wide.py",
)
POLICY_FIELDS = (
    "k",
    "max_required_shared",
    "min_target_kmer_fraction",
    "fragment_policy",
)
_DIGEST = re.compile(r"[a-f0-9]{64}\Z")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fingerprint(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _physical(path: Path) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Expected physical plan/code file: {path}")
    return path.resolve(strict=True)


def _input(path: Path) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"Broken input path: {path}") from exc
    if not resolved.is_file():
        raise ValueError(f"Expected regular input: {path}")
    return resolved


def _release_sources(run: Path, role: str) -> dict[str, dict[str, str]]:
    base = run / "work/pipeline-v2"
    files = {
        "targets": base / "common/01/manifest.json",
        "go_positive": base / role / "01C/manifest.json",
        "taxonomy": base / role / "02B/manifest.json",
    }
    sources = {}
    manifests = {}
    for label, path in files.items():
        real = _input(path)
        manifest = StageManifest.read(real)
        expected = {"targets": "01", "go_positive": "01C", "taxonomy": "02B"}
        manifests[label] = manifest
        if manifest.stage != expected[label]:
            raise ValueError(f"Wrong upstream stage: {label}")
        if manifest.role != (None if label == "targets" else role):
            raise ValueError(f"Wrong upstream role: {label}")
        sources[label] = {
            "path": str(path),
            "resolved": str(real),
            "sha256": sha256(real),
        }
    positive = manifests["go_positive"]
    taxonomy = manifests["taxonomy"]
    if positive.release_id is None or positive.release_id != taxonomy.release_id:
        raise ValueError("Stage 2C upstream release mismatch")
    if positive.ontology != taxonomy.ontology:
        raise ValueError("Stage 2C upstream ontology mismatch")
    return sources


def _scan_parameters(run: Path) -> dict:
    path = _input(run / "pipeline.stage08.collection.yaml")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Invalid run configuration")
    sequence = data["scientific"]["sequence"]
    parameters = {key: sequence[key] for key in POLICY_FIELDS}
    if parameters["fragment_policy"] != "exclude":
        raise ValueError("Species-wide primary scan must exclude FRAG=1")
    if not isinstance(parameters["k"], int) or parameters["k"] < 1:
        raise ValueError("Invalid k-mer length")
    parameters["taxonomy_policy"] = "species_wide"
    return parameters


def _binary(repo: Path) -> Path:
    return repo / (
        ".pixi/build/probe-primary-candidate-scan-v2/probe-primary-candidate-scan-v2"
    )


def build_plan(repo: Path, run: Path) -> dict:
    repo = repo.resolve(strict=True)
    run = run.resolve(strict=True)
    if not (repo / ".git").exists():
        check = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            check=True,
        )
        if check.stdout.strip() != "true":
            raise ValueError("Not a Git repository")
    binary = _physical(_binary(repo))
    code = {name: sha256(_physical(repo / name)) for name in CODE_FILES}
    sources = {role: _release_sources(run, role) for role in ROLES}
    parameters = _scan_parameters(run)
    fingerprint_input = {
        "schema": SCHEMA,
        "science": SCIENCE,
        "sources": sources,
        "parameters": parameters,
        "code": code,
        "binary_sha256": sha256(binary),
    }
    key = fingerprint(fingerprint_input)
    paths = {
        role: str(run / "work/pipeline-v2" / role / "03_species_wide_variants" / key)
        for role in ROLES
    }
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return {
        **fingerprint_input,
        "key": key,
        "repo": str(repo),
        "run": str(run),
        "binary": str(binary),
        "git_head_at_creation": head,
        "paths": paths,
    }


def verify_plan(spec: dict) -> None:
    if spec.get("schema") != SCHEMA or spec.get("science") != SCIENCE:
        raise ValueError("Unexpected species-wide discovery schema")
    repo = Path(spec["repo"])
    run = Path(spec["run"])
    regenerated = build_plan(repo, run)
    for key in (
        "science",
        "sources",
        "parameters",
        "code",
        "binary_sha256",
        "key",
        "binary",
        "paths",
        "repo",
        "run",
    ):
        if regenerated[key] != spec.get(key):
            raise ValueError(f"Species-wide pinned plan changed: {key}")
    for role in ROLES:
        destination = Path(spec["paths"][role])
        parent = run / "work/pipeline-v2" / role / "03_species_wide_variants"
        if destination.parent != parent or destination.name != spec["key"]:
            raise ValueError("Unsafe species-wide output path")
        current = destination
        while current != run:
            if current.is_symlink():
                raise ValueError("Publication cannot use symlinked paths")
            current = current.parent
        if not destination.exists():
            continue
        if not destination.is_dir() or not any(destination.iterdir()):
            continue
        if not (destination / "manifest.json").is_file():
            raise ValueError("Unpublished nonempty discovery directory")
        published = validate_bundle(destination, deep=False)
        if (
            published.stage != "03"
            or published.role != role
            or published.implementation.get("discovery_fingerprint") != spec["key"]
        ):
            raise ValueError("Incompatible existing discovery publication")


def read_plan(path: Path) -> dict:
    obj = json.loads(_physical(path).read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError("Species-wide plan must be a JSON mapping")
    verify_plan(obj)
    return obj


def create_plan(repo: Path, run: Path) -> Path:
    spec = build_plan(repo, run)
    key = spec["key"]
    destination = (
        Path(spec["run"])
        / "experiments"
        / (f"species-wide-discovery-{key[:16]}")
        / "discovery.json"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(spec, sort_keys=True, indent=2) + "\n"
    if destination.exists() or destination.is_symlink():
        try:
            previous = read_plan(destination)
        except (ValueError, TypeError, KeyError) as exc:
            raise FileExistsError(
                "Existing discovery plan is invalid or incompatible"
            ) from exc
        if previous["key"] != key:
            raise FileExistsError("Existing discovery plan has different content")
        return destination
    fd, temporary_name = tempfile.mkstemp(
        prefix=".discovery-", suffix=".json", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError as exc:
            raise FileExistsError("Concurrent discovery plan creation") from exc
    finally:
        temporary.unlink(missing_ok=True)
    return destination
