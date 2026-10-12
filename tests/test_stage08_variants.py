"""Synthetic evidence-only experiment identity and cleanup contracts."""

import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from probe.stage08_variants import (
    check_edges,
    create,
    list_variants,
    read_spec,
    remove_variant,
)


def write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding="utf-8")


@pytest.fixture
def setup(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    run = tmp_path / "run"
    repo.mkdir()
    run.mkdir()
    write(
        repo / "config/pipeline.yaml",
        "releases:\n  START:\n    release_id: '2023_01'\n"
        "  END:\n    release_id: '2026_03'\n",
    )
    write(run / "pipeline.stage08.collection.yaml", "{}\n")
    common = run / "results/pipeline-v2/common"
    write(
        common / "08_interpret_compact/manifest.json",
        json.dumps({"ontology_sha256": ""}),
    )
    write(
        common / "08/08_collection_summary.json",
        json.dumps(
            {
                "by_role": {
                    "START": {"release": "2023_01"},
                    "END": {"release": "2026_03"},
                },
            }
        ),
    )
    write(run / "inputs/targets.tsv", "a\tb\n")
    write(run / "work/pipeline-v2/common/01/01_targets.tsv", "a\tb\n")
    policy = repo / "config/policy.json"
    registry = repo / "config/registry.json"
    ontology = repo / "config/go.owl"
    write(policy, '{"classes": {}}')
    write(registry, '{"codes": []}')
    write(ontology, "TEST OWL")
    from probe.stage08_variants import digest_file

    manifest = common / "08_interpret_compact/manifest.json"
    write(manifest, json.dumps({"ontology_sha256": digest_file(ontology)}))
    for relative in (
        "scripts/pipeline/08_build_benchmark_v2.py",
        "src/probe/stage08_temporal_v2.py",
        "scripts/pipeline/08_profile_benchmark.py",
        "src/probe/stage08_variants.py",
        "src/probe/stage08_temporal_benchmark.py",
        "src/probe/stage08_evidence_registry.py",
    ):
        write(repo / relative, "# test module\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=t@x",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=repo,
        check=True,
    )
    fake = ModuleType("probe.stage08_temporal_v2")
    fake.load_classes = lambda *args: None
    monkeypatch.setitem(sys.modules, "probe.stage08_temporal_v2", fake)
    return repo, run, policy, registry, ontology


def build(setup, **kwargs):
    repo, run, policy, registry, ontology = setup
    return create(
        repo=repo,
        run=run,
        name=kwargs.pop("name", "baseline"),
        policy=policy,
        registry=registry,
        ontology=ontology,
        workflow_config=run / "pipeline.stage08.collection.yaml",
        **kwargs,
    )


def test_create_and_snapshots(setup):
    spec = build(setup)
    repo, run, *_ = setup
    item = run / "experiments" / spec["experiment_id"]
    assert read_spec(item / "config.resolved.json") == spec
    assert "benchmark_variant_spec:" in (item / "experiment.yaml").read_text()
    assert (item / "policy.json").is_file()
    assert (item / "PARAMETERS.md").is_file()
    assert not Path(spec["benchmark"]).exists()
    assert len(list_variants(run)) == 1
    assert repo.is_dir()


def test_second_experiment_reuses_benchmark(setup):
    original = build(setup)
    filtered = build(setup, name="longer", min_length=1023)
    assert original["benchmark_key"] == filtered["benchmark_key"]
    assert original["diagnostic_key"] != filtered["diagnostic_key"]
    assert len(list_variants(setup[1])) == 2
    preview = remove_variant(
        setup[1], original["experiment_id"], delete_unreferenced=True
    )
    assert original["benchmark"] not in preview["artifacts_to_delete"]
    assert original["statistics"] in preview["artifacts_to_delete"]
    assert preview["dry_run"]


def test_remove_requires_confirmation(setup):
    spec = build(setup)
    run = setup[1]
    with pytest.raises(ValueError, match="Confirmation"):
        remove_variant(run, spec["experiment_id"], confirm="wrong")
    assert (run / "experiments" / spec["experiment_id"]).is_dir()
    outcome = remove_variant(run, spec["experiment_id"], confirm=spec["experiment_id"])
    assert not outcome["dry_run"]
    assert not list_variants(run)


def test_pinned_inputs_must_remain_unchanged(setup):
    spec = build(setup)
    run = setup[1]
    target = run / "inputs/targets.tsv"
    target.write_text("MUTATED\n")
    with pytest.raises(ValueError, match="source changed"):
        read_spec(run / "experiments" / spec["experiment_id"] / "config.resolved.json")


@pytest.mark.parametrize("value", ["1,4", "0,5,5", "0,9,3", "", "0"])
def test_bad_length_edges(value):
    with pytest.raises(ValueError):
        check_edges(value)


def test_new_scientific_policy_changes_artifact(setup):
    first = build(setup)
    repo, run, policy, *_ = setup
    policy.write_text('{"classes": {"SCORE": []}}\n')
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=t@x",
            "commit",
            "-qm",
            "second policy",
        ],
        cwd=repo,
        check=True,
    )
    second = build(setup, name="altered")
    assert first["benchmark_key"] != second["benchmark_key"]
    assert first["policy_sha256"] != second["policy_sha256"]
    assert len(list_variants(run)) == 2


def test_taxon_filters_change_diagnostics_only(setup):
    first = build(setup)
    second = build(setup, name="human", taxids="9606,10090,9606")
    assert first["benchmark_key"] == second["benchmark_key"]
    assert first["diagnostic_key"] != second["diagnostic_key"]
    assert second["filters"]["taxids"] == "9606,10090"


def test_reject_incorrect_taxon_filter(setup):
    with pytest.raises(ValueError, match="TaxIDs"):
        build(setup, taxids="9606,unknown")


def test_workflow_has_opt_in_variant_path():
    root = Path(__file__).resolve().parents[1]
    source = (root / "workflow/Snakefile").read_text(encoding="utf-8")
    assert 'config.pop("benchmark_variant_spec", None)' in source
    assert "rule stage_08_variant_diagnostics:" in source
    assert "rule publish_stage_08_benchmark_variant:" in source
    assert "rule publish_stage_08_diagnostics_variant:" in source
    assert "rule stage_07:" in source
    assert "rule stage_08:" in source
