"""Synthetic tests for the explicit Stage 08 collection entry point."""

import importlib.util
import json
from pathlib import Path

import pytest
from test_stage08_collection_publish import _setup

from probe.workflow.artifacts import validate_bundle

CLI_PATH = (
    Path(__file__).resolve().parents[1] / "scripts/pipeline/08_collect_annotations.py"
)


def _cli():
    spec = importlib.util.spec_from_file_location("probe_stage08_cli", CLI_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _arguments(paths) -> list[str]:
    flags = (
        ("--stage07-bundle", "stage07_bundle"),
        ("--start-goa-bundle", "start_goa_bundle"),
        ("--end-goa-bundle", "end_goa_bundle"),
        ("--go-policy-bundle", "go_policy_bundle"),
        ("--destination", "destination"),
    )
    return [value for flag, key in flags for value in (flag, str(paths[key]))]


def test_default_preflight_does_not_open_gaf_or_write(tmp_path, capsys, monkeypatch):
    paths = _setup(tmp_path)
    cli = _cli()

    def fail_if_called(**_kwargs):
        pytest.fail("default entry point must never publish")

    monkeypatch.setattr(cli, "publish_raw_annotation_collection", fail_if_called)
    assert cli.main(_arguments(paths)) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["mode"] == "PREFLIGHT_ONLY"
    assert report["normalized_gaf_contents_read"] is False
    assert report["output_published"] is False
    assert report["collection_input_report"]["associations_loaded"] == 7
    assert not paths["destination"].exists()


def test_explicit_execute_publishes_synthetic_bundle(tmp_path, capsys):
    paths = _setup(tmp_path)
    cli = _cli()
    assert cli.main([*_arguments(paths), "--execute"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["mode"] == "EXECUTED"
    assert report["bundle_stage"] == "08"
    assert report["manifest_status"] == "complete"
    assert report["output_artifact_count"] == 12
    assert validate_bundle(paths["destination"], deep=True).status == "complete"


def test_execute_cannot_replace_valid_bundle(tmp_path):
    paths = _setup(tmp_path)
    cli = _cli()
    assert cli.main([*_arguments(paths), "--execute"]) == 0
    with pytest.raises(FileExistsError):
        cli.main([*_arguments(paths), "--execute"])
    assert validate_bundle(paths["destination"], deep=True).status == "complete"


def test_preflight_can_inspect_completed_destination_without_mutation(tmp_path, capsys):
    paths = _setup(tmp_path)
    cli = _cli()
    cli.main([*_arguments(paths), "--execute"])
    before = (paths["destination"] / "manifest.json").read_bytes()
    capsys.readouterr()
    cli.main(_arguments(paths))
    assert json.loads(capsys.readouterr().out)["mode"] == "PREFLIGHT_ONLY"
    assert (paths["destination"] / "manifest.json").read_bytes() == before


def test_missing_bundle_and_missing_options_fail(tmp_path):
    paths = _setup(tmp_path)
    cli = _cli()
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 2
    paths["stage07_bundle"] = tmp_path / "not-there"
    with pytest.raises((FileNotFoundError, OSError)):
        cli.main(_arguments(paths))


def test_help_does_not_scan(capsys):
    cli = _cli()
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    assert "--execute" in capsys.readouterr().out
