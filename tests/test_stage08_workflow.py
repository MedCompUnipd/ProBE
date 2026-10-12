"""Stage 08 scheduling guard and bounded synthetic workflow-runner tests."""

import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from probe.workflow.config import PipelineConfig

RUNNER = Path(__file__).resolve().parents[1] / "workflow/scripts/run_stage_08.py"
CONFIG = Path(__file__).resolve().parents[1] / "config/pipeline.yaml"


def settings() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def test_stage08_defaults_to_fail_closed() -> None:
    prepared = PipelineConfig.from_mapping(settings())
    assert prepared.stage08_collection_parameters() == {"execute_full_scan": False}
    assert "stage08_collection" not in prepared.stage07_parameters()


def test_stage08_opt_in_does_not_change_stage07_parameters() -> None:
    original = PipelineConfig.from_mapping(settings())
    changed = settings()
    changed["stage08_collection"] = {"execute_full_scan": True}
    updated = PipelineConfig.from_mapping(changed)
    assert updated.stage08_collection_parameters() == {"execute_full_scan": True}
    assert updated.stage07_parameters() == original.stage07_parameters()
    assert updated.stage03_parameters() == original.stage03_parameters()


@pytest.mark.parametrize(
    "value",
    [
        None,
        0,
        1,
        "true",
        {},
        {"execute_full_scan": "true"},
        {"execute_full_scan": False, "extra": 1},
    ],
)
def test_invalid_stage08_collection_config_rejected(value) -> None:
    config = settings()
    config["stage08_collection"] = value
    with pytest.raises(ValueError, match="stage08_collection"):
        PipelineConfig.from_mapping(config)


def context(tmp_path: Path, *, execute: bool) -> SimpleNamespace:
    work = tmp_path / "work"
    results = tmp_path / "results"
    work.mkdir()
    results.mkdir()
    inputs = {
        "stage07_manifest": results / "common/07/manifest.json",
        "start_goa_manifest": work / "START/01B1/manifest.json",
        "end_goa_manifest": work / "END/01B1/manifest.json",
        "go_policy_manifest": work / "common/01B0/manifest.json",
    }
    for filename in inputs.values():
        filename.parent.mkdir(parents=True)
        filename.write_text("fixture\n", encoding="utf-8")
    inputs["implementation_script"] = (
        Path(__file__).resolve().parents[1]
        / "scripts/pipeline/08_collect_annotations.py"
    )
    inputs["runner_script"] = RUNNER
    return SimpleNamespace(
        params=SimpleNamespace(
            stage08_collection={"execute_full_scan": execute},
            work_root=str(work),
            results_root=str(results),
        ),
        output=[str(results / "common/08/manifest.json")],
        input=SimpleNamespace(**{k: str(v) for k, v in inputs.items()}),
    )


def execute_runner(snakemake) -> None:
    runpy.run_path(str(RUNNER), init_globals={"snakemake": snakemake})


def test_default_runner_does_not_open_or_publish(tmp_path, monkeypatch) -> None:
    import probe.stage08_collection_publish as publisher

    def forbidden(**kwargs):
        pytest.fail("collection executed without explicit workflow opt-in")

    monkeypatch.setattr(publisher, "publish_raw_annotation_collection", forbidden)
    with pytest.raises(RuntimeError, match="disabled"):
        execute_runner(context(tmp_path, execute=False))


def test_workflow_runner_refuses_completed_bundle(tmp_path, monkeypatch) -> None:
    import probe.stage08_collection_publish as publisher

    monkeypatch.setattr(
        publisher,
        "publish_raw_annotation_collection",
        lambda **kwargs: pytest.fail("completed bundle was overwritten"),
    )
    snakemake = context(tmp_path, execute=True)
    destination = Path(snakemake.output[0]).parent
    destination.mkdir(parents=True)
    (destination / "manifest.json").write_text("complete")
    with pytest.raises(FileExistsError, match="completed bundle"):
        execute_runner(snakemake)


def test_invalidated_bundle_rejects_unexpected_files(tmp_path, monkeypatch) -> None:
    import probe.stage08_collection_publish as publisher

    monkeypatch.setattr(
        publisher,
        "publish_raw_annotation_collection",
        lambda **kwargs: pytest.fail("invalid directory unexpectedly cleared"),
    )
    snakemake = context(tmp_path, execute=True)
    destination = Path(snakemake.output[0]).parent
    destination.mkdir(parents=True)
    (destination / "unrelated.txt").write_text("do not delete")
    with pytest.raises(FileExistsError, match="unexpected item"):
        execute_runner(snakemake)
    assert (destination / "unrelated.txt").exists()


def test_explicit_opt_in_calls_m4_with_workflow_invalidation(tmp_path, monkeypatch):
    import probe.stage08_collection_publish as publisher

    seen = []

    def fake_publish(**kwargs):
        seen.append(kwargs)
        return SimpleNamespace(stage="08", status="complete")

    monkeypatch.setattr(publisher, "publish_raw_annotation_collection", fake_publish)
    snakemake = context(tmp_path, execute=True)
    execute_runner(snakemake)
    assert len(seen) == 1
    assert seen[0]["allow_invalidated_destination"] is True
    assert seen[0]["destination"] == Path(snakemake.output[0]).parent
    assert seen[0]["stage07_bundle"].name == "07"
    assert seen[0]["start_goa_bundle"].name == "01B1"
    assert seen[0]["end_goa_bundle"].name == "01B1"
    assert seen[0]["implementation_sources"] == {
        "scripts/pipeline/08_collect_annotations.py": Path(
            snakemake.input.implementation_script
        ),
        "workflow/scripts/run_stage_08.py": Path(snakemake.input.runner_script),
    }


def test_wrapper_is_snakemake_script_compatible() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    assert "from __future__" not in source
    assert 'globals()["snakemake"]' in source
