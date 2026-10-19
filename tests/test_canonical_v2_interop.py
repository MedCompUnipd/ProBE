"""Synthetic safety and compatibility tests for the B3-to-06C1 handoff."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from probe.canonical_v2.interop import (
    SOURCE_TO_PREVIEW,
    publish_completion_input,
    validate_b3_stage,
)
from probe.canonical_v2.stage05_06a import STAGE_SCHEMA, SUMMARY
from probe.stage2c_alignment_completion import _preview
from probe.stage2c_alignment_preview import SUMMARY_OUT


def _fixture(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    directory = tmp_path / "b3"
    directory.mkdir()
    releases = {"START": "R1", "END": "R2"}
    outputs = {*SOURCE_TO_PREVIEW, "05C2.summary.json"}
    for name in outputs:
        (directory / name).write_text("test\n", encoding="utf-8")
    (directory / "06A.summary.json").write_text(
        json.dumps({
            "stage": "06A",
            "releases": releases,
            "policy": {"same_entity_decision_made_here": False},
        }),
        encoding="utf-8",
    )
    checksums = {
        name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
        for name in outputs
    }
    (directory / SUMMARY).write_text(
        json.dumps({
            "schema": STAGE_SCHEMA,
            "releases": releases,
            "mode": "cold_start",
            "go_donor_decisions": 0,
            "dp_executions": 0,
            "historical_05_07_inputs": 0,
            "outputs_sha256": checksums,
        }),
        encoding="utf-8",
    )
    return directory, releases


def test_handoff_preserves_six_payloads(tmp_path: Path) -> None:
    b3, releases = _fixture(tmp_path)
    target = tmp_path / "preview"
    result = publish_completion_input(
        b3=b3, releases=releases, destination=target
    )
    metadata, files = _preview(target)
    assert metadata == result
    assert set(files) == set(SOURCE_TO_PREVIEW.values()) - {
        "2C_06A_summary.json"
    }
    for name, output in SOURCE_TO_PREVIEW.items():
        assert (b3 / name).read_bytes() == (target / output).read_bytes()
    assert (target / SUMMARY_OUT).is_file()


def test_source_digest_mutation_rejected(tmp_path: Path) -> None:
    b3, releases = _fixture(tmp_path)
    (b3 / next(iter(SOURCE_TO_PREVIEW))).write_text("altered\n")
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        validate_b3_stage(b3, releases)


def test_extra_file_rejected(tmp_path: Path) -> None:
    b3, releases = _fixture(tmp_path)
    (b3 / "unknown.tsv").write_text("unexpected\n")
    with pytest.raises(ValueError, match="Unexpected B3 publication"):
        validate_b3_stage(b3, releases)


def test_refuses_destination_overwrite(tmp_path: Path) -> None:
    b3, releases = _fixture(tmp_path)
    with pytest.raises(FileExistsError, match="occupied"):
        publish_completion_input(b3=b3, releases=releases, destination=b3)


def test_wrong_release_rejected(tmp_path: Path) -> None:
    b3, releases = _fixture(tmp_path)
    releases["END"] = "WRONG"
    with pytest.raises(ValueError, match="release mismatch"):
        validate_b3_stage(b3, releases)
