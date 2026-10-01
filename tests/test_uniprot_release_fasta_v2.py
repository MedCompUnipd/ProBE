# ruff: noqa: E501
"""Regression coverage for the native UniProt DAT-to-enriched-FASTA converter."""

from __future__ import annotations

import gzip
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
FIXTURE_DIR = ROOT / "tests/fixtures/uniprot_release_fasta_v2"
FIXTURE_NAME = "synthetic.dat"
FIXTURE = FIXTURE_DIR / FIXTURE_NAME
EXPECTED_FASTA = FIXTURE_DIR / "synthetic.expected.fasta"
PYTHON_REFERENCE = ROOT / "TESTING/build_probe_release_fasta_v2.py"
BUILD_DIR = ROOT / ".pixi/build/probe-release-fasta"
BINARY_NAME = (
    "probe-release-fasta-v2.exe" if os.name == "nt" else "probe-release-fasta-v2"
)

EXPECTED_TOTALS = {
    "flag_combinations": {
        "Fragment": 1,
        "Fragments": 1,
        "NONE": 1,
        "Precursor": 1,
        "Precursor|Fragment": 1,
        "Precursor|Fragments": 1,
    },
    "fragment_records": 4,
    "length_mismatch_records": 1,
    "missing_taxid_records": 1,
    "multiple_taxid_records": 1,
    "non_fragment_records": 2,
    "records_seen": 9,
    "records_skipped": 4,
    "records_with_gene_metadata": 2,
    "records_with_multiple_gene_groups": 1,
    "records_with_secondary_accessions": 1,
    "records_written": 6,
    "reviewed_records": 4,
    "unknown_section_records": 1,
    "unreviewed_records": 1,
}


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
def converter_binary() -> Path:
    subprocess.run(
        [str(_pixi_executable()), "run", "build-release-fasta"],
        cwd=ROOT,
        check=True,
    )
    binary = BUILD_DIR / BINARY_NAME
    assert binary.is_file(), f"Pixi build did not produce {binary}"
    return binary


def _run_converter(
    executable: list[str],
    input_arguments: list[str],
    output_prefix: Path,
    *,
    cwd: Path,
) -> tuple[bytes, bytes, dict[str, object]]:
    command = executable.copy()
    for input_argument in input_arguments:
        command.extend(("--input", input_argument))
    command.extend(
        (
            "--output-prefix",
            str(output_prefix),
            "--release-id",
            "SYNTHETIC",
            "--progress-every",
            "0",
        )
    )
    subprocess.run(command, cwd=cwd, check=True)
    return (
        Path(f"{output_prefix}.fasta").read_bytes(),
        Path(f"{output_prefix}.issues.tsv").read_bytes(),
        json.loads(Path(f"{output_prefix}.summary.json").read_text()),
    )


def _expected_input(path: str, source_index: int) -> dict[str, object]:
    return {**EXPECTED_TOTALS, "path": path, "source_index": source_index}


def _expected_issues(input_path: str) -> bytes:
    columns = (
        "source_index",
        "source_path",
        "source_record",
        "source_line",
        "rid",
        "issue",
        "detail",
        "primary_accession",
        "entry_name",
    )
    rows = (
        ("0", input_path, "2", "12", "2", "MULTIPLE_TAXIDS", "111,222", "A00002", "UNREVIEWED_TWO"),
        ("0", input_path, "3", "24", "3", "MISSING_TAXID", "No NCBI_TaxID found in OX", "P00003", "PRECURSOR_ONLY"),
        ("0", input_path, "3", "24", "3", "SEQUENCE_LENGTH_MISMATCH", "SQ=6;parsed=5", "P00003", "PRECURSOR_ONLY"),
        ("0", input_path, "7", "54", "", "RECORD_SKIPPED", "missing primary accession", "", "NO_ACCESSION"),
        ("0", input_path, "8", "58", "", "RECORD_SKIPPED", "missing ID/entry name", "P00007", ""),
        ("0", input_path, "9", "63", "", "RECORD_SKIPPED", "missing sequence", "P00008", "NO_SEQUENCE"),
        ("0", input_path, "10", "67", "", "UNTERMINATED_RECORD", "EOF reached before //", "P00009", "UNTERMINATED"),
    )
    return (
        "\t".join(columns) + "\n" + "\n".join("\t".join(row) for row in rows) + "\n"
    ).encode()


def _assert_single_source_summary(summary: dict[str, object], input_path: str) -> None:
    assert summary["format_version"] == 2
    assert summary["release_id"] == "SYNTHETIC"
    assert summary["fragment_policy"] == "STRICT_DE_FLAGS_FRAGMENT_OR_FRAGMENTS_ONLY"
    assert summary["totals"] == EXPECTED_TOTALS
    assert summary["inputs"] == [_expected_input(input_path, 0)]


def test_native_converter_matches_committed_expected_bytes(
    tmp_path: Path, converter_binary: Path
) -> None:
    fasta, issues, summary = _run_converter(
        [str(converter_binary)],
        [FIXTURE_NAME],
        tmp_path / "native",
        cwd=FIXTURE_DIR,
    )

    assert fasta == EXPECTED_FASTA.read_bytes()
    assert issues == _expected_issues(FIXTURE_NAME)
    assert len(fasta.splitlines()) == 12
    assert all(line.startswith(b">") for line in fasta.splitlines()[::2])
    assert b"FLAGS=Precursor,Fragments" in fasta
    _assert_single_source_summary(summary, FIXTURE_NAME)


def test_native_converter_reads_gzip(tmp_path: Path, converter_binary: Path) -> None:
    input_path = tmp_path / "synthetic.dat.gz"
    with (
        FIXTURE.open("rb") as source,
        gzip.GzipFile(input_path, "wb", mtime=0) as target,
    ):
        shutil.copyfileobj(source, target)

    input_argument = str(input_path)
    fasta, issues, summary = _run_converter(
        [str(converter_binary)],
        [input_argument],
        tmp_path / "native-gzip",
        cwd=ROOT,
    )
    assert fasta == EXPECTED_FASTA.read_bytes()
    assert issues == _expected_issues(input_argument)
    _assert_single_source_summary(summary, input_argument)


def test_native_converter_preserves_input_order_and_continuous_rids(
    tmp_path: Path, converter_binary: Path
) -> None:
    fasta, _, summary = _run_converter(
        [str(converter_binary)],
        [FIXTURE_NAME, FIXTURE_NAME],
        tmp_path / "native-two-sources",
        cwd=FIXTURE_DIR,
    )

    headers = fasta.decode().splitlines()[::2]
    assert [
        headers[0].split()[1],
        headers[5].split()[1],
        headers[6].split()[1],
        headers[11].split()[1],
    ] == ["RID=1", "RID=6", "RID=7", "RID=12"]
    assert all("SRC=0" in header for header in headers[:6])
    assert all("SRC=1" in header for header in headers[6:])
    assert summary["inputs"] == [
        _expected_input(FIXTURE_NAME, 0),
        _expected_input(FIXTURE_NAME, 1),
    ]
    assert summary["totals"] == {
        key: value * 2
        if isinstance(value, int)
        else {name: count * 2 for name, count in value.items()}
        for key, value in EXPECTED_TOTALS.items()
    }


@pytest.mark.skipif(
    not PYTHON_REFERENCE.is_file(),
    reason="optional local TESTING Python oracle is unavailable",
)
def test_optional_local_python_oracle_parity(
    tmp_path: Path, converter_binary: Path
) -> None:
    reference = _run_converter(
        [sys.executable, str(PYTHON_REFERENCE)],
        [FIXTURE_NAME],
        tmp_path / "python-reference",
        cwd=FIXTURE_DIR,
    )
    native = _run_converter(
        [str(converter_binary)],
        [FIXTURE_NAME],
        tmp_path / "native-reference-parity",
        cwd=FIXTURE_DIR,
    )

    assert native[:2] == reference[:2]
    for summary in (reference[2], native[2]):
        _assert_single_source_summary(summary, FIXTURE_NAME)
