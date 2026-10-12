"""Tests for Snakemake-created directories and M12 manifest reuse."""

import runpy
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import probe.stage08_variants as variants

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("kind", ["benchmark", "statistics"])
@pytest.mark.parametrize(
    "condition", ["absent", "empty", "nonempty", "symlink", "file"]
)
def test_immutable_publication_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    condition: str,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    dest = tmp_path / "artifacts" / "variant"
    dest.parent.mkdir()
    outputs = {
        "benchmark": (
            "run_stage_08_benchmark_variant.py",
            "benchmark_manifest",
            "manifest.json",
        ),
        "statistics": (
            "run_stage_08_diagnostics_variant.py",
            "diagnostics_manifest",
            "publication_manifest.json",
        ),
    }
    script_name, output_key, manifest_name = outputs[kind]
    if condition == "symlink":
        other = tmp_path / "other"
        other.mkdir()
        dest.symlink_to(other, target_is_directory=True)
    elif condition == "file":
        dest.write_text("preserve", encoding="utf-8")
    elif condition != "absent":
        dest.mkdir()
        if condition == "nonempty":
            (dest / "preserve.txt").write_text("preserve", encoding="utf-8")

    spec = {
        "repo": str(repo),
        "benchmark": str(dest if kind == "benchmark" else tmp_path / "input"),
        "statistics": str(dest if kind == "statistics" else tmp_path / "output"),
        "registry": str(tmp_path / "registry.json"),
        "policy": str(tmp_path / "policy.json"),
        "sources": {
            "m10c_manifest": str(tmp_path / "m10c" / "manifest.json"),
            "m8_summary": str(tmp_path / "m8.json"),
            "targets": str(tmp_path / "targets.tsv"),
            "stage01": str(tmp_path / "stage01.tsv"),
            "ontology": str(tmp_path / "go.owl"),
        },
        "releases": {"START": "2023_01", "END": "2026_03"},
        "filters": {
            "min_length": None,
            "max_length": None,
            "taxids": None,
            "length_edges": "0,300,600,1023,1500,3000",
        },
    }
    context = SimpleNamespace(
        params=SimpleNamespace(spec_path=str(tmp_path / "spec.json")),
        output=SimpleNamespace(**{output_key: str(dest / manifest_name)}),
        log=[str(tmp_path / "execution.log")],
    )
    monkeypatch.setattr(variants, "read_spec", lambda unused: spec)
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        assert not dest.exists()
        dest.mkdir()
        (dest / manifest_name).write_text("{}\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    script = ROOT / "workflow" / "scripts" / script_name
    if condition in {"absent", "empty"}:
        runpy.run_path(str(script), init_globals={"snakemake": context})
        assert len(calls) == 1
        assert (dest / manifest_name).is_file()
    else:
        with pytest.raises(FileExistsError):
            runpy.run_path(str(script), init_globals={"snakemake": context})
        assert not calls
        if condition == "nonempty":
            assert (dest / "preserve.txt").read_text() == "preserve"
        elif condition == "symlink":
            assert dest.is_symlink()
        else:
            assert dest.read_text() == "preserve"


def test_m12_manifest_reuse_keys() -> None:
    snakefile = (ROOT / "workflow" / "Snakefile").read_text()
    assert 'published.get("registry_sha256")' not in snakefile
    assert 'published.get("registry_fingerprint")' in snakefile
    assert 'published.get("class_fingerprint")' in snakefile
