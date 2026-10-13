"""Independent, pinned union-plan contract and publication safeguards."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import probe.stage2c_union_plan as union
from probe.workflow.artifacts import Artifact, StageManifest, sha256_file


def _taxonomy(path: Path, role: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    file = path.parent / "02B_target_taxonomy.tsv"
    file.write_text(
        "target_id\tresolved_taxid\tspecies_taxid\nT1\t111\t562\n",
        encoding="utf-8",
    )
    manifest = StageManifest(
        stage="02B",
        role=role,
        release_id="2023_01",
        parameters={},
        ontology=None,
        implementation={"name": "test", "version": "1"},
        inputs=(),
        outputs=(Artifact(file.name, file.stat().st_size, sha256_file(file)),),
    )
    manifest.write(path)


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "repo"
    run = tmp_path / "run"
    repo.mkdir()
    run.mkdir()
    for name in union.CODE_FILES:
        file = repo / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(f"source for {name}\n", encoding="utf-8")
    species_file = run / "species.json"
    full_file = run / "full.json"
    species_file.write_text("{}", encoding="utf-8")
    full_file.write_text("{}", encoding="utf-8")
    species = {
        "repo": str(repo),
        "run": str(run),
        "key": "a" * 64,
        "sources": {},
        "paths": {},
    }
    full = {
        "repo": str(repo),
        "run": str(run),
        "key": "b" * 64,
        "sources": {},
        "paths": {},
    }
    for role in union.ROLES:
        taxonomy = run / "work/pipeline-v2" / role / "02B/manifest.json"
        _taxonomy(taxonomy, role)
        meta = {
            "path": str(taxonomy),
            "resolved": str(taxonomy.resolve()),
            "sha256": union.sha256(taxonomy),
        }
        species["sources"][role] = {"taxonomy": meta}
        full["sources"][role] = {"taxonomy": dict(meta), "release_id": "2023_01"}
        species["paths"][role] = str(run / role / "species")
        full["paths"][role] = str(run / role / "full")
    monkeypatch.setattr(union, "read_species_plan", lambda _p: species)
    monkeypatch.setattr(union, "read_full_plan", lambda _p: full)
    return repo, run, species_file, full_file, species, full


def test_plan_is_shared_by_donor_policies(project) -> None:
    repo, _, species, full, _, _ = project
    path = union.create_plan(repo, species, full)
    assert path == union.create_plan(repo, species, full)
    spec = union.read_plan(path)
    assert spec["paths"]["START"] != spec["paths"]["END"]
    assert "subtaxon_policy" not in json.dumps(spec)
    assert spec["species_key"] == "a" * 64
    assert spec["full_key"] == "b" * 64


def test_rejects_mismatched_taxonomy(project) -> None:
    repo, _, species, full, _, full_data = project
    full_data["sources"]["END"]["taxonomy"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="taxonomy mismatch"):
        union.build_plan(repo, species, full)


def test_change_in_implementation_invalidates_plan(project) -> None:
    repo, _, species, full, _, _ = project
    path = union.create_plan(repo, species, full)
    (repo / union.CODE_FILES[0]).write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="[Pp]inned.*changed"):
        union.read_plan(path)


def test_nonempty_unpublished_destination_refused(project) -> None:
    repo, _, species, full, _, _ = project
    path = union.create_plan(repo, species, full)
    result = union.read_plan(path)
    destination = Path(result["paths"]["START"])
    destination.mkdir(parents=True)
    (destination / "orphan").write_text("x", encoding="utf-8")
    with pytest.raises(FileExistsError, match="unpublished"):
        union.read_plan(path)


def test_symlinked_destination_refused(project) -> None:
    repo, run, species, full, _, _ = project
    path = union.create_plan(repo, species, full)
    result = union.read_plan(path)
    destination = Path(result["paths"]["START"])
    destination.parent.mkdir(parents=True)
    target = run / "elsewhere"
    target.mkdir()
    destination.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="Symlinked"):
        union.read_plan(path)


def test_unexpected_existing_plan_refused(project) -> None:
    repo, _, species, full, _, _ = project
    path = union.create_plan(repo, species, full)
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        union.create_plan(repo, species, full)


def test_synthetic_runner_publishes_atomic_union(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import csv
    import runpy
    from types import SimpleNamespace

    from probe.workflow.artifacts import validate_bundle

    root = tmp_path / "work"
    root.mkdir()
    role = "START"
    release = "2023_01"
    destination = root / "union"
    go = root / "go"
    full = root / "full"
    taxonomy_bundle = root / "taxonomy"
    for directory in (go, full, taxonomy_bundle):
        directory.mkdir()
    samples = (
        (
            go,
            "03_primary_candidates.pairs.tsv",
            "target_id\trid\taccession\traw_ox\tresolved_taxid\t"
            "species_taxid\tfrag\texact_sequence\trecord_number\n"
            "T1\t21\tA21\t112\t112\t100\t0\t0\t1\n",
            "03",
            "species",
        ),
        (
            full,
            "03_full_exact_cross_strain.pairs.tsv",
            "release\ttarget_id\trecord_number\trid\taccession\t"
            "raw_ox\ttarget_resolved_taxid\tcandidate_resolved_taxid\t"
            "species_taxid\tfrag\tlength_aa\texact_full_sequence\t"
            "discovery_channel\n"
            "2023_01\tT1\t44\t24\tA24\t113\t111\t113\t"
            "100\t0\t20\t1\tFULL_V2_EXACT\n",
            "03_FULL_EXACT",
            "full",
        ),
        (
            taxonomy_bundle,
            "02B_target_taxonomy.tsv",
            "target_id\tresolved_taxid\tspecies_taxid\nT1\t111\t100\n",
            "02B",
            "taxonomy",
        ),
    )
    for directory, name, content, stage, fingerprint in samples:
        file = directory / name
        file.write_text(content, encoding="utf-8")
        implementation = {"name": "test", "version": "1"}
        if fingerprint != "taxonomy":
            implementation["discovery_fingerprint"] = fingerprint
        StageManifest(
            stage=stage,
            role=role,
            release_id=release,
            parameters={},
            ontology=None,
            implementation=implementation,
            inputs=(),
            outputs=(Artifact(name, file.stat().st_size, sha256_file(file)),),
        ).write(directory / "manifest.json")
    spec = {
        "paths": {role: str(destination)},
        "species_paths": {role: str(go)},
        "full_paths": {role: str(full)},
        "taxonomy": {
            role: {
                "resolved": str(taxonomy_bundle / "manifest.json"),
                "sha256": sha256_file(taxonomy_bundle / "manifest.json"),
            }
        },
        "releases": {role: release},
        "key": "union",
        "species_key": "species",
        "full_key": "full",
    }
    monkeypatch.setattr(union, "read_plan", lambda _path: spec)
    context = SimpleNamespace(
        params=SimpleNamespace(role=role, plan_path=str(root / "spec.json")),
        input=SimpleNamespace(
            go_manifest=str(go / "manifest.json"),
            full_manifest=str(full / "manifest.json"),
            taxonomy_manifest=str(taxonomy_bundle / "manifest.json"),
        ),
        output=SimpleNamespace(union_manifest=str(destination / "manifest.json")),
        log=[str(root / "union.log")],
    )
    source = Path(__file__).resolve().parents[1]
    runner = source / "workflow/scripts/run_stage_03_discovery_union.py"
    runpy.run_path(str(runner), init_globals={"snakemake": context})
    manifest = validate_bundle(destination, deep=True)
    assert manifest.stage == "03_UNION"
    with (destination / union.PAIRS).open() as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    assert len(rows) == 2
    assert {row["rid"] for row in rows} == {"21", "24"}
    assert rows[1]["same_species_exact_gate"] == "ELIGIBLE"
    with pytest.raises(FileExistsError):
        runpy.run_path(str(runner), init_globals={"snakemake": context})


def test_unresolved_target_taxonomy_is_not_a_candidate(tmp_path: Path) -> None:
    from probe.stage2c_candidate_union import _taxonomy

    path = tmp_path / "taxonomy.tsv"
    path.write_text(
        "target_id\tresolved_taxid\tspecies_taxid\nT1\t111\t100\nT2\t\t\n",
        encoding="utf-8",
    )
    assert _taxonomy(path) == {"T1": ("111", "100")}
