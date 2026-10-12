"""Independent Stage 08 executable provenance regression tests."""

import pytest
from test_stage08_collection_cli import CLI_PATH, _arguments, _cli
from test_stage08_collection_publish import _run, _setup

from probe import stage08_collection_publish as publisher
from probe.workflow.artifacts import StageManifest, sha256_file


def test_cli_sha256_is_published_in_synthetic_manifest(tmp_path, capsys):
    paths = _setup(tmp_path)
    assert _cli().main([*_arguments(paths), "--execute"]) == 0
    manifest = StageManifest.read(paths["destination"] / "manifest.json")
    assert manifest.implementation["source_sha256"][
        "scripts/pipeline/08_collect_annotations.py"
    ] == sha256_file(CLI_PATH)


def test_explicit_runner_sha256_in_manifest(tmp_path):
    paths = _setup(tmp_path)
    source = tmp_path / "wrapper.py"
    source.write_text("print('stage08')\n", encoding="utf-8")
    manifest = _run(
        paths,
        implementation_sources={"workflow/scripts/run_stage_08.py": source},
    )
    assert manifest.implementation["source_sha256"][
        "workflow/scripts/run_stage_08.py"
    ] == sha256_file(source)


def test_runner_mutation_prevents_publish(tmp_path, monkeypatch):
    paths = _setup(tmp_path)
    source = tmp_path / "wrapper.py"
    source.write_text("print('stage08')\n", encoding="utf-8")
    real = publisher.collect_one_normalized_gaf

    def change_after_end(*, gaf, **kwargs):
        result = real(gaf=gaf, **kwargs)
        if gaf.role == "END":
            source.write_text("print('changed')\n", encoding="utf-8")
        return result

    monkeypatch.setattr(publisher, "collect_one_normalized_gaf", change_after_end)
    with pytest.raises(ValueError, match="implementation changed"):
        _run(
            paths,
            implementation_sources={"workflow/scripts/run_stage_08.py": source},
        )
    assert not paths["destination"].exists()


def test_unknown_implementation_source_rejected(tmp_path):
    paths = _setup(tmp_path)
    source = tmp_path / "wrapper.py"
    source.write_text("print('stage08')\n", encoding="utf-8")
    with pytest.raises(ValueError, match="implementation source"):
        _run(paths, implementation_sources={"unknown.py": source})
    assert not paths["destination"].exists()
