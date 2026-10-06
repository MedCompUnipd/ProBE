"""Regression coverage for the native Stage 01B1 GOA normalizer."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUILD_DIR = ROOT / ".pixi/build/probe-release-fasta"
BINARY_NAME = (
    "probe-goa-normalize-v1.exe"
    if os.name == "nt"
    else "probe-goa-normalize-v1"
)


def _pixi_executable() -> Path:
    configured = os.environ.get("PIXI_EXE")
    if configured:
        return Path(configured)

    discovered = shutil.which("pixi")
    if discovered:
        return Path(discovered)

    executable_name = "pixi.exe" if os.name == "nt" else "pixi"
    candidate = Path.home() / ".pixi/bin" / executable_name

    if candidate.is_file():
        return candidate

    pytest.fail("Pixi executable not found")


@pytest.fixture(scope="session")
def normalizer_binary() -> Path:
    subprocess.run(
        [str(_pixi_executable()), "run", "build-goa-normalize"],
        cwd=ROOT,
        check=True,
    )

    binary = BUILD_DIR / BINARY_NAME
    assert binary.is_file()
    return binary


def _gaf_row(
    db: str,
    accession: str,
    qualifier: str,
    go_id: str,
    aspect: str,
) -> str:
    columns = [
        db,
        accession,
        "OBJ",
        qualifier,
        go_id,
        "REF",
        "ECO",
        "WITH",
        aspect,
        "NAME",
        "SYN",
        "protein",
        "taxon:1",
        "20260101",
        "UniProt",
        "",
        "",
    ]

    assert len(columns) == 17
    return "\t".join(columns)


def test_native_goa_normalizer_semantics(
    tmp_path: Path,
    normalizer_binary: Path,
) -> None:
    output = tmp_path / "out"
    output.mkdir()

    policy = tmp_path / "policy.tsv"
    policy.write_text(
        "input_go_id\tcanonical_go_id\tresolution_status\t"
        "namespace\tis_root\treject_reason\n"
        "GO:1\tGO:1\tactive\tbiological_process\t0\t\n"
        "GO:2\tGO:0008150\treplaced\tbiological_process\t1\t\n"
        "GO:3\t\tobsolete\t\t0\tOBSOLETE_GO\n"
        "GO:4\t\tdeprecated\t\t0\tDEPRECATED_GO\n"
        "GO:5\tGO:6\treplaced\tmolecular_function\t0\t\n",
        encoding="utf-8",
    )

    v2 = tmp_path / "v2.fasta"
    v2.write_text(
        ">sp|A1|E1 RID=1\nAAAA\n"
        ">sp|A2|E2 RID=2\nAAAA\n"
        ">sp|A3|E3 RID=3\nAAAA\n"
        ">sp|A4|E4 RID=4\nAAAA\n",
        encoding="utf-8",
    )

    goa = tmp_path / "goa.gaf"
    lines = [
        "!gaf-version: 2.2",
        _gaf_row("UniProtKB", "A3", "", "GO:2", "P"),
        _gaf_row("UniProtKB", "A2", "NOT", "GO:1", "P"),
        _gaf_row("UniProtKB", "A1", "", "GO:1", "P"),
        _gaf_row("UniProtKB", "A2", "", "GO:3", "P"),
        _gaf_row("UniProtKB", "A4", "NOT", "GO:1", "P"),
        _gaf_row("UniProtKB", "A2", "", "GO:1", "P"),
        _gaf_row("UniProtKB", "A1", "", "GO:5", "F"),
        _gaf_row("UniProtKB", "A1", "", "GO:5", "P"),
        _gaf_row("UniProtKB", "A1", "", "GO:4", "P"),
        _gaf_row("UniProtKB", "A1", "", "GO:999", "P"),
        _gaf_row("ComplexPortal", "CPX", "", "GO:1", "P"),
        _gaf_row("UniProtKB", "MISSING", "", "GO:3", "P"),
    ]
    goa.write_text("\n".join(lines) + "\n", encoding="utf-8")

    subprocess.run(
        [
            str(normalizer_binary),
            "--policy",
            str(policy),
            "--goa",
            str(goa),
            "--uniprot-v2",
            str(v2),
            "--output-dir",
            str(output),
            "--reserve-v2-records",
            "4",
            "--progress-every",
            "0",
        ],
        check=True,
    )

    assert (
        output / "goa.positive_accessions.txt"
    ).read_text(encoding="utf-8") == "A1\nA2\n"

    assert (
        output / "goa.only_not_accessions.txt"
    ).read_text(encoding="utf-8") == "A4\n"

    assert (
        output / "goa.missing_accessions.txt"
    ).read_text(encoding="utf-8") == "MISSING\n"

    stats = json.loads(
        (output / "goa.stats.json").read_text(encoding="utf-8")
    )

    assert stats["stage"] == "01B"
    assert stats["implementation_stage"] == "01B1"
    assert stats["fasta_accession_count"] == 4
    assert stats["goa_accession_count"] == 5
    assert stats["shared_accession_count"] == 4
    assert stats["goa_only_accession_count"] == 1
    assert stats["uniprot_only_accession_count"] == 0
    assert stats["retained_accession_count"] == 3
    assert stats["positive_accession_count"] == 2
    assert stats["negative_accession_count"] == 2
    assert stats["only_not_accession_count"] == 1
    assert stats["root_only_accession_count"] == 1
    assert stats["input_data_row_count"] == 12
    assert stats["retained_annotation_count"] == 5
    assert stats["positive_annotation_count"] == 3
    assert stats["not_annotation_count"] == 2
    assert stats["remapped_annotation_count"] == 1

    assert stats["discard_reason_counts"] == {
        "ASPECT_MISMATCH": 1,
        "DEPRECATED_GO": 1,
        "GOA_ONLY": 1,
        "IGNORED_DB": 1,
        "OBSOLETE_GO": 1,
        "ROOT_GO": 1,
        "UNKNOWN_GO": 1,
    }

    normalized = (
        output / "goa.normalized.gaf"
    ).read_text(encoding="utf-8")

    assert "GO:6" in normalized
    assert "MISSING" not in normalized
    assert "ComplexPortal" not in normalized
    assert "GO:999" not in normalized
