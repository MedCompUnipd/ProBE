"""Synthetic immutable discovery-plan tests; no real UniProt release reads."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from probe.stage2c_discovery import (
    CODE_FILES,
    build_plan,
    create_plan,
    read_plan,
)
from probe.workflow.artifacts import StageManifest


def _manifest(path: Path, *, stage: str, role: str | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest = StageManifest(
        stage=stage,
        role=role,
        release_id=None if role is None else "2023_01",
        parameters={},
        ontology=None,
        implementation={"name": "synthetic", "version": "1"},
        inputs=(),
        outputs=(),
    )
    data = dict(manifest.__dict__)
    data["inputs"] = []
    data["outputs"] = [{"path": "placeholder", "size_bytes": 0, "sha256": None}]
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def project(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    run = tmp_path / "run"
    repo.mkdir()
    run.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.org",
            "commit",
            "--allow-empty",
            "-qm",
            "Synthetic fixture",
        ],
        check=True,
    )
    for name in CODE_FILES:
        file = repo / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(name, encoding="utf-8")
    binary = repo / (
        ".pixi/build/probe-primary-candidate-scan-v2/probe-primary-candidate-scan-v2"
    )
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"mock-binary")
    yaml_file = run / "pipeline.stage08.collection.yaml"
    yaml_file.write_text(
        "scientific:\n"
        "  sequence:\n"
        "    k: 7\n"
        "    max_required_shared: 10\n"
        "    min_target_kmer_fraction: 0.25\n"
        "    fragment_policy: exclude\n",
        encoding="utf-8",
    )
    base = run / "work/pipeline-v2"
    _manifest(base / "common/01/manifest.json", stage="01", role=None)
    for role in ("START", "END"):
        _manifest(base / role / "01C/manifest.json", stage="01C", role=role)
        _manifest(base / role / "02B/manifest.json", stage="02B", role=role)
    return repo, run


def test_plan_is_policy_independent_and_reused(
    project: tuple[Path, Path],
) -> None:
    repo, run = project
    one = create_plan(repo, run)
    two = create_plan(repo, run)
    assert one == two
    plan = read_plan(one)
    assert plan["parameters"]["taxonomy_policy"] == "species_wide"
    assert plan["paths"]["START"] != plan["paths"]["END"]
    assert plan["key"] in plan["paths"]["START"]
    assert build_plan(repo, run)["key"] == plan["key"]


def test_upstream_manifest_change_invalidates_plan(
    project: tuple[Path, Path],
) -> None:
    repo, run = project
    path = create_plan(repo, run)
    source = run / "work/pipeline-v2/END/02B/manifest.json"
    source.write_text(source.read_text(encoding="utf-8") + " ")
    with pytest.raises(ValueError, match="pinned plan changed"):
        read_plan(path)


def test_code_change_invalidates_plan(project: tuple[Path, Path]) -> None:
    repo, run = project
    path = create_plan(repo, run)
    (repo / CODE_FILES[0]).write_text("different", encoding="utf-8")
    with pytest.raises(ValueError, match="pinned plan changed"):
        read_plan(path)


def test_existing_plan_never_overwritten(project: tuple[Path, Path]) -> None:
    repo, run = project
    path = create_plan(repo, run)
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(FileExistsError, match="invalid or incompatible"):
        create_plan(repo, run)


def test_output_symlink_refused(project: tuple[Path, Path]) -> None:
    repo, run = project
    path = create_plan(repo, run)
    plan = read_plan(path)
    output = Path(plan["paths"]["START"])
    output.parent.mkdir(parents=True, exist_ok=True)
    other = run / "safekeeping"
    other.mkdir()
    output.symlink_to(other, target_is_directory=True)
    with pytest.raises(ValueError, match="symlinked paths"):
        read_plan(path)


def test_unknown_target_does_not_require_accession(
    project: tuple[Path, Path],
) -> None:
    repo, run = project
    plan = build_plan(repo, run)
    assert "accession" not in plan["parameters"]


def test_unrelated_git_commit_does_not_invalidate_discovery(
    project: tuple[Path, Path],
) -> None:
    repo, run = project
    existing = create_plan(repo, run)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.org",
            "commit",
            "--allow-empty",
            "-qm",
            "Unrelated documentation",
        ],
        check=True,
    )
    assert create_plan(repo, run) == existing
    assert read_plan(existing)["key"] == build_plan(repo, run)["key"]


def test_nonempty_unpublished_directory_refused(
    project: tuple[Path, Path],
) -> None:
    repo, run = project
    path = create_plan(repo, run)
    plan = read_plan(path)
    destination = Path(plan["paths"]["END"])
    destination.mkdir(parents=True)
    (destination / "sentinel.txt").write_text("do not remove", encoding="utf-8")
    with pytest.raises(ValueError, match="Unpublished nonempty"):
        read_plan(path)
    assert (destination / "sentinel.txt").is_file()
