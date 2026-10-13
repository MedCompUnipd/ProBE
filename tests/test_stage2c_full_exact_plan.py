"""FULL exact discovery plan, immutable publication, and synthetic execution."""

from __future__ import annotations

import csv
import json
import runpy
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from probe.stage2c_full_exact import (
    BINARY,
    CODE_FILES,
    build_plan,
    create_plan,
    read_plan,
)
from probe.workflow.artifacts import (
    Artifact,
    StageManifest,
    sha256_file,
    validate_bundle,
)


def _publish(bundle: Path, stage: str, role: str | None, name: str) -> None:
    bundle.mkdir(parents=True)
    file = bundle / name
    if name.endswith(".fasta"):
        file.write_text(
            ">sp|P00001|PROT OX=112 FRAG=0 RID=1 SRC=SP REC=R\nACDEFGHIK\n",
            encoding="utf-8",
        )
    elif name == "01_targets.tsv":
        file.write_text(
            "target_id\tnormalized_sequence\nT1\tACDEFGHIK\n",
            encoding="utf-8",
        )
    elif name == "02B_target_taxonomy.tsv":
        file.write_text(
            "target_id\tresolved_taxid\tspecies_taxid\tprimary_bucket\n"
            "T1\t111\t562\tT:111\n",
            encoding="utf-8",
        )
    else:
        file.write_text(
            "raw_ox\tresolved_taxid\tspecies_taxid\tspecies_bucket\n"
            "112\t112\t562\tS:562\n",
            encoding="utf-8",
        )
    artifact = Artifact(name, file.stat().st_size, sha256_file(file))
    manifest = StageManifest(
        stage=stage,
        role=role,
        release_id=None if role is None else "2023_01",
        parameters={},
        ontology=None,
        implementation={"name": "fixture", "version": "1"},
        inputs=(),
        outputs=(artifact,),
    )
    manifest.write(bundle / "manifest.json")


@pytest.fixture
def project(tmp_path: Path) -> tuple[Path, Path]:
    repo, run = tmp_path / "repo", tmp_path / "run"
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
            "fixture",
        ],
        check=True,
    )
    original_repo = Path(__file__).resolve().parents[1]
    for name in CODE_FILES:
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original_repo / name, target)
    binary = repo / BINARY
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"synthetic executable")
    base = run / "work/pipeline-v2"
    _publish(base / "common/01", "01", None, "01_targets.tsv")
    for role in ("START", "END"):
        root = base / role
        _publish(root / "01A_FULL", "01A_FULL", role, "01A_full_v2.fasta")
        _publish(root / "02B", "02B", role, "02B_target_taxonomy.tsv")
        _publish(root / "04D0b", "04D0b", role, "full_ox_taxonomy.tsv")
    return repo, run


def test_plan_is_shared_across_resolver_policies(project: tuple[Path, Path]) -> None:
    repo, run = project
    plan_path = create_plan(repo, run)
    plan = read_plan(plan_path)
    assert plan_path == create_plan(repo, run)
    assert "subtaxon_policy" not in json.dumps(plan["parameters"])
    assert plan["paths"]["START"] != plan["paths"]["END"]
    assert build_plan(repo, run)["key"] == plan["key"]


def test_manifest_change_invalidates_plan(project: tuple[Path, Path]) -> None:
    repo, run = project
    plan_path = create_plan(repo, run)
    manifest = run / "work/pipeline-v2/START/04D0b/manifest.json"
    manifest.write_text(manifest.read_text(encoding="utf-8") + " ")
    with pytest.raises(ValueError, match="pinned plan changed"):
        read_plan(plan_path)


def test_code_change_invalidates_plan(project: tuple[Path, Path]) -> None:
    repo, run = project
    plan_path = create_plan(repo, run)
    (repo / CODE_FILES[0]).write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="pinned plan changed"):
        read_plan(plan_path)


def test_existing_plan_never_replaced(project: tuple[Path, Path]) -> None:
    repo, run = project
    plan_path = create_plan(repo, run)
    plan_path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        create_plan(repo, run)


def test_symlinked_input_manifest_is_valid(project: tuple[Path, Path]) -> None:
    repo, run = project
    original = run / "work/pipeline-v2/START/04D0b/manifest.json"
    target = original.with_name("real-manifest.json")
    original.rename(target)
    original.symlink_to(target.name)
    assert build_plan(repo, run)["sources"]["START"]["ox"]["resolved"] == (str(target))


def test_output_symlink_fails_closed(project: tuple[Path, Path]) -> None:
    repo, run = project
    plan_path = create_plan(repo, run)
    plan = read_plan(plan_path)
    output = Path(plan["paths"]["START"])
    output.parent.mkdir(parents=True)
    other = run / "other"
    other.mkdir()
    output.symlink_to(other, target_is_directory=True)
    with pytest.raises(ValueError, match="Symlink"):
        read_plan(plan_path)


def test_nonempty_unpublished_output_fails_closed(
    project: tuple[Path, Path],
) -> None:
    repo, run = project
    plan_path = create_plan(repo, run)
    output = Path(read_plan(plan_path)["paths"]["END"])
    output.mkdir(parents=True)
    (output / "orphan").write_text("data", encoding="utf-8")
    with pytest.raises(ValueError, match="unpublished"):
        read_plan(plan_path)


def test_release_mismatch_is_refused(project: tuple[Path, Path]) -> None:
    repo, run = project
    path = run / "work/pipeline-v2/START/04D0b/manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["release_id"] = "2026_03"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="release mismatch"):
        build_plan(repo, run)


def test_synthetic_runner_and_immutable_reuse(
    project: tuple[Path, Path],
) -> None:
    repo, run = project
    compiler = shutil.which("g++") or shutil.which("c++")
    if compiler is None:
        pytest.skip("C++20 compiler unavailable")
    source = Path(__file__).resolve().parents[1]
    binary = repo / BINARY
    subprocess.run(
        [
            compiler,
            "-std=c++20",
            "-O1",
            str(source / "cpp/src/probe_full_exact_cross_strain_v1.cpp"),
            "-o",
            str(binary),
        ],
        check=True,
    )
    path = create_plan(repo, run)
    plan = read_plan(path)
    base = run / "work/pipeline-v2"
    context = SimpleNamespace(
        params=SimpleNamespace(role="START", plan_path=str(path)),
        input=SimpleNamespace(
            target_manifest=str(base / "common/01/manifest.json"),
            full_manifest=str(base / "START/01A_FULL/manifest.json"),
            taxonomy_manifest=str(base / "START/02B/manifest.json"),
            ox_manifest=str(base / "START/04D0b/manifest.json"),
            binary=str(binary),
        ),
        output=[str(Path(plan["paths"]["START"]) / "manifest.json")],
        log=[str(run / "logs/scan.log")],
    )
    runner = source / "workflow/scripts/run_stage_03_full_exact.py"
    runpy.run_path(str(runner), init_globals={"snakemake": context})
    output = Path(plan["paths"]["START"])
    manifest = validate_bundle(output, deep=True)
    assert manifest.stage == "03_FULL_EXACT"
    assert manifest.implementation["discovery_fingerprint"] == plan["key"]
    with (output / "03_full_exact_cross_strain.pairs.tsv").open() as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    assert len(rows) == 1
    assert rows[0]["target_id"] == "T1"
    assert rows[0]["exact_full_sequence"] == "1"
    with pytest.raises(FileExistsError):
        runpy.run_path(str(runner), init_globals={"snakemake": context})
