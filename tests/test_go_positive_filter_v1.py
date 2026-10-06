import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUILD_DIR = ROOT / ".pixi/build/probe-go-positive-filter"
BINARY = BUILD_DIR / "probe-go-positive-filter-v1"


def _pixi_executable() -> str:
    configured = os.environ.get("PIXI_EXE")
    if configured:
        return configured

    executable = shutil.which("pixi")
    if executable is None:
        pytest.skip("pixi executable is unavailable")

    return executable


@pytest.fixture(scope="session")
def filter_binary() -> Path:
    subprocess.run(
        [_pixi_executable(), "run", "build-go-positive-filter"],
        cwd=ROOT,
        check=True,
    )

    if not BINARY.is_file():
        raise AssertionError(f"expected native binary: {BINARY}")

    return BINARY


def _write_v2(path: Path) -> None:
    path.write_text(
        ">sp|P00001|ONE OX=9606 FRAG=1\n"
        "AAAA\n"
        ">tr|Q00002|TWO OX=9606 FRAG=0\n"
        "CCCCC\n"
        ">sp|R00003|THREE OX=9606 FRAG=0\n"
        "MMMMMM\n",
        encoding="utf-8",
    )


def _run(
    binary: Path,
    input_v2: Path,
    positives: Path,
    output_v2: Path,
    stats: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(binary),
            "--input-v2",
            str(input_v2),
            "--positive-accessions",
            str(positives),
            "--output-v2",
            str(output_v2),
            "--stats",
            str(stats),
            "--progress-every",
            "0",
        ],
        text=True,
        capture_output=True,
        check=False,
    )


def test_exact_positive_membership_preserves_records(
    filter_binary: Path,
    tmp_path: Path,
) -> None:
    input_v2 = tmp_path / "full.fasta"
    positives = tmp_path / "positive.txt"
    output_v2 = tmp_path / "positive.fasta"
    stats = tmp_path / "stats.tsv"

    _write_v2(input_v2)
    positives.write_text("Q00002\nP00001\n", encoding="utf-8")

    result = _run(
        filter_binary,
        input_v2,
        positives,
        output_v2,
        stats,
    )

    assert result.returncode == 0, result.stderr

    assert output_v2.read_text(encoding="utf-8") == (
        ">sp|P00001|ONE OX=9606 FRAG=1\n"
        "AAAA\n"
        ">tr|Q00002|TWO OX=9606 FRAG=0\n"
        "CCCCC\n"
    )

    assert stats.read_text(encoding="utf-8") == (
        "metric\tvalue\n"
        "positive_whitelist_count\t2\n"
        "input_v2_record_count\t3\n"
        "output_v2_record_count\t2\n"
        "selected_sequence_residue_count\t9\n"
        "whitelist_missing_from_v2\t0\n"
    )


def test_missing_positive_accession_fails_closed(
    filter_binary: Path,
    tmp_path: Path,
) -> None:
    input_v2 = tmp_path / "full.fasta"
    positives = tmp_path / "positive.txt"
    output_v2 = tmp_path / "positive.fasta"
    stats = tmp_path / "stats.tsv"

    _write_v2(input_v2)
    positives.write_text("P00001\nMISSING\n", encoding="utf-8")

    result = _run(
        filter_binary,
        input_v2,
        positives,
        output_v2,
        stats,
    )

    assert result.returncode == 2
    assert "positive whitelist contains accessions absent from V2" in result.stderr
    assert not output_v2.exists()
    assert not stats.exists()


def test_duplicate_positive_accession_is_rejected(
    filter_binary: Path,
    tmp_path: Path,
) -> None:
    input_v2 = tmp_path / "full.fasta"
    positives = tmp_path / "positive.txt"
    output_v2 = tmp_path / "positive.fasta"
    stats = tmp_path / "stats.tsv"

    _write_v2(input_v2)
    positives.write_text("P00001\nP00001\n", encoding="utf-8")

    result = _run(
        filter_binary,
        input_v2,
        positives,
        output_v2,
        stats,
    )

    assert result.returncode == 2
    assert "duplicate accession in positive whitelist" in result.stderr
    assert not output_v2.exists()
    assert not stats.exists()


def test_unknown_section_record_is_valid_v2(
    filter_binary: Path,
    tmp_path: Path,
) -> None:
    input_v2 = tmp_path / "full.fasta"
    positives = tmp_path / "positive.txt"
    output_v2 = tmp_path / "positive.fasta"
    stats = tmp_path / "stats.tsv"

    record = (
        ">unk|U00004|UNKNOWN_SECTION OX=562 FRAG=0\n"
        "ACDEFG\n"
    )

    input_v2.write_text(record, encoding="utf-8")
    positives.write_text("U00004\n", encoding="utf-8")

    result = _run(
        filter_binary,
        input_v2,
        positives,
        output_v2,
        stats,
    )

    assert result.returncode == 0, result.stderr
    assert output_v2.read_text(encoding="utf-8") == record

    assert stats.read_text(encoding="utf-8") == (
        "metric\tvalue\n"
        "positive_whitelist_count\t1\n"
        "input_v2_record_count\t1\n"
        "output_v2_record_count\t1\n"
        "selected_sequence_residue_count\t6\n"
        "whitelist_missing_from_v2\t0\n"
    )
