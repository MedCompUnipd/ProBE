"""Tests for deterministic GO normalization-policy compilation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from probe.go_policy import (
    POLICY_FILENAME,
    SCIENTIFIC_MANIFEST_FILENAME,
    STATS_FILENAME,
    compile_go_policy,
)
from probe.ontology import GeneOntology, OntologyTerm


@pytest.fixture
def synthetic_ontology(monkeypatch: pytest.MonkeyPatch) -> GeneOntology:
    ontology = GeneOntology(
        [
            OntologyTerm(
                "GO:0003674",
                namespace="molecular_function",
            ),
            OntologyTerm(
                "GO:1234567",
                namespace="molecular_function",
                alternate_ids=("GO:7654321",),
            ),
            OntologyTerm(
                "GO:1111111",
                namespace="molecular_function",
                obsolete=True,
            ),
            OntologyTerm(
                "GO:2222222",
                namespace="molecular_function",
                deprecated=True,
            ),
            OntologyTerm(
                "GO:3333333",
                namespace="molecular_function",
                obsolete=True,
                replaced_by=("GO:1234567",),
            ),
        ]
    )

    monkeypatch.setattr(
        GeneOntology,
        "from_owl",
        classmethod(lambda cls, source: ontology),
    )
    return ontology


def _owl(tmp_path: Path) -> tuple[Path, str]:
    path = tmp_path / "go.owl"
    payload = b"synthetic-go.owl\n"
    path.write_bytes(payload)
    return path, hashlib.sha256(payload).hexdigest()


def test_compile_without_expected_checksum(
    tmp_path: Path,
    synthetic_ontology: GeneOntology,
) -> None:
    owl, observed_sha256 = _owl(tmp_path)
    output = tmp_path / "output"

    result = compile_go_policy(
        owl_path=owl,
        output_directory=output,
    )

    assert result.policy_row_count == 6
    assert result.usable_resolution_count == 4
    assert result.remapped_resolution_count == 2

    stats = json.loads((output / STATS_FILENAME).read_text())
    assert stats["ontology_sha256"] == observed_sha256
    assert stats["policy_row_count"] == 6
    assert stats["usable_resolution_count"] == 4
    assert stats["remapped_resolution_count"] == 2
    assert stats["root_resolution_count"] == 1
    assert stats["resolution_status_counts"] == {
        "active": 2,
        "alternate_id": 1,
        "deprecated": 1,
        "obsolete": 1,
        "replaced": 1,
    }
    assert stats["reject_reason_counts"] == {
        "DEPRECATED_GO": 1,
        "OBSOLETE_GO": 1,
    }

    manifest = json.loads(
        (output / SCIENTIFIC_MANIFEST_FILENAME).read_text()
    )
    assert manifest["ontology_sha256"] == observed_sha256


def test_expected_checksum_does_not_change_outputs(
    tmp_path: Path,
    synthetic_ontology: GeneOntology,
) -> None:
    owl, observed_sha256 = _owl(tmp_path)

    unpinned = tmp_path / "unpinned"
    checked = tmp_path / "checked"

    compile_go_policy(
        owl_path=owl,
        output_directory=unpinned,
    )
    compile_go_policy(
        owl_path=owl,
        output_directory=checked,
        expected_owl_sha256=observed_sha256,
    )

    for filename in (
        POLICY_FILENAME,
        STATS_FILENAME,
        SCIENTIFIC_MANIFEST_FILENAME,
    ):
        assert (unpinned / filename).read_bytes() == (
            checked / filename
        ).read_bytes()


def test_rejects_invalid_expected_checksum(tmp_path: Path) -> None:
    owl, _ = _owl(tmp_path)

    with pytest.raises(
        ValueError,
        match="64-digit hexadecimal SHA-256",
    ):
        compile_go_policy(
            owl_path=owl,
            output_directory=tmp_path / "output",
            expected_owl_sha256="not-a-sha256",
        )


def test_rejects_checksum_mismatch(tmp_path: Path) -> None:
    owl, _ = _owl(tmp_path)

    with pytest.raises(ValueError, match="GO SHA-256 mismatch"):
        compile_go_policy(
            owl_path=owl,
            output_directory=tmp_path / "output",
            expected_owl_sha256="0" * 64,
        )


def test_refuses_existing_outputs(
    tmp_path: Path,
    synthetic_ontology: GeneOntology,
) -> None:
    owl, _ = _owl(tmp_path)
    output = tmp_path / "output"

    compile_go_policy(
        owl_path=owl,
        output_directory=output,
    )

    with pytest.raises(FileExistsError):
        compile_go_policy(
            owl_path=owl,
            output_directory=output,
        )
