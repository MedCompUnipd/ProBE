import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUILD_DIR = ROOT / ".pixi/build/probe-raw-ox-extract"
BINARY = BUILD_DIR / "probe-raw-ox-extract-v1"


def _pixi_executable() -> str:
    configured = os.environ.get("PIXI_EXE")
    if configured:
        return configured

    executable = shutil.which("pixi")
    if executable is None:
        pytest.skip("pixi executable is unavailable")

    return executable


@pytest.fixture(scope="session")
def extractor_binary() -> Path:
    subprocess.run(
        [_pixi_executable(), "run", "build-raw-ox-extract"],
        cwd=ROOT,
        check=True,
    )

    if not BINARY.is_file():
        raise AssertionError(f"expected native binary: {BINARY}")

    return BINARY


def _run(
    binary: Path,
    input_v2: Path,
    output: Path,
    stats: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(binary),
            "--input-v2",
            str(input_v2),
            "--output",
            str(output),
            "--stats",
            str(stats),
            "--progress-every",
            "0",
        ],
        text=True,
        capture_output=True,
        check=False,
    )


def test_extracts_distinct_raw_ox_deterministically(
    extractor_binary: Path,
    tmp_path: Path,
) -> None:
    input_v2 = tmp_path / "positive.fasta"
    output = tmp_path / "raw_ox.txt"
    stats = tmp_path / "stats.tsv"

    input_v2.write_text(
        ">sp|P00001|ONE OX=9606 FRAG=0\n"
        "AAAA\n"
        ">tr|P00002|TWO OX=111,222 FRAG=0\n"
        "CCCCC\n"
        ">unk|P00003|THREE OX=NA FRAG=0\n"
        "MMMMMM\n"
        ">sp|P00004|FOUR OX=9606 FRAG=1\n"
        "TTT\n",
        encoding="utf-8",
    )

    result = _run(
        extractor_binary,
        input_v2,
        output,
        stats,
    )

    assert result.returncode == 0, result.stderr

    assert output.read_text(encoding="utf-8") == (
        "111,222\n"
        "9606\n"
        "NA\n"
    )

    assert stats.read_text(encoding="utf-8") == (
        "metric\tvalue\n"
        "input_v2_record_count\t4\n"
        "distinct_raw_ox_count\t3\n"
    )


def test_missing_ox_fails_closed(
    extractor_binary: Path,
    tmp_path: Path,
) -> None:
    input_v2 = tmp_path / "positive.fasta"
    output = tmp_path / "raw_ox.txt"
    stats = tmp_path / "stats.tsv"

    input_v2.write_text(
        ">sp|P00001|ONE FRAG=0\n"
        "AAAA\n",
        encoding="utf-8",
    )

    result = _run(
        extractor_binary,
        input_v2,
        output,
        stats,
    )

    assert result.returncode == 2
    assert "V2 header missing OX token" in result.stderr
    assert not output.exists()
    assert not stats.exists()


def test_malformed_two_line_v2_fails_closed(
    extractor_binary: Path,
    tmp_path: Path,
) -> None:
    input_v2 = tmp_path / "positive.fasta"
    output = tmp_path / "raw_ox.txt"
    stats = tmp_path / "stats.tsv"

    input_v2.write_text(
        ">sp|P00001|ONE OX=9606 FRAG=0\n",
        encoding="utf-8",
    )

    result = _run(
        extractor_binary,
        input_v2,
        output,
        stats,
    )

    assert result.returncode == 2
    assert "V2 format violation: missing sequence line" in result.stderr
    assert not output.exists()
    assert not stats.exists()
