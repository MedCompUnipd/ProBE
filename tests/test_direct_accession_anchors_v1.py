import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BINARY = (
    ROOT
    / ".pixi"
    / "build"
    / "probe-direct-accession-anchors"
    / "probe-direct-accession-anchors-v1"
)


@pytest.fixture(scope="session")
def scanner_binary() -> Path:
    if not BINARY.is_file():
        pytest.fail(
            "missing 04A binary; run "
            "`pixi run build-direct-accession-anchors` first"
        )
    return BINARY


def read_tsv(path: Path) -> list[dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    header = lines[0].split("\t")
    return [
        dict(zip(header, line.split("\t"), strict=True))
        for line in lines[1:]
    ]


def read_summary(path: Path) -> dict[str, str]:
    return {
        row["metric"]: row["value"]
        for row in read_tsv(path)
    }


def run_scanner(
    binary: Path,
    tmp_path: Path,
    targets_text: str,
    fasta_text: str,
) -> subprocess.CompletedProcess[str]:
    targets = tmp_path / "targets.tsv"
    fasta = tmp_path / "full_v2.fasta"
    output = tmp_path / "anchors.tsv"
    summary = tmp_path / "summary.tsv"

    targets.write_text(targets_text, encoding="utf-8")
    fasta.write_text(fasta_text, encoding="utf-8")

    return subprocess.run(
        [
            str(binary),
            "--targets",
            str(targets),
            "--fasta",
            str(fasta),
            "--release",
            "SYNTHETIC",
            "--output",
            str(output),
            "--summary",
            str(summary),
            "--progress-every",
            "0",
        ],
        text=True,
        capture_output=True,
        check=False,
    )


def test_primary_secondary_fragment_and_unk_are_preserved(
    scanner_binary: Path,
    tmp_path: Path,
) -> None:
    targets = (
        "target_id\toptional_accession\n"
        "T_PRIMARY\tP11111\n"
        "T_SECONDARY\tQ22222\n"
        "T_NONE\tZ99999\n"
        "T_EMPTY\t\n"
    )

    fasta = (
        ">sp|P11111|ENTRY1 RID=1 SRC=sp REC=Reviewed OX=9606 "
        "FRAG=0 LEN=10 PE=1 SV=1 SEC=. GENE=.\n"
        "ACDEFGHIKL\n"
        ">unk|X33333|ENTRY2 RID=2 SRC=unk REC=Unknown OX=10090 "
        "FRAG=1 LEN=10 PE=. SV=. SEC=Q22222 GENE=.\n"
        "MNPQRSTVWY\n"
    )

    result = run_scanner(
        scanner_binary,
        tmp_path,
        targets,
        fasta,
    )

    assert result.returncode == 0, result.stderr

    rows = read_tsv(tmp_path / "anchors.tsv")
    assert len(rows) == 2

    assert rows[0]["target_id"] == "T_PRIMARY"
    assert rows[0]["direct_match_type"] == "PRIMARY"
    assert rows[0]["direct_match_accession"] == "P11111"
    assert rows[0]["primary_accession"] == "P11111"
    assert rows[0]["frag"] == "0"

    assert rows[1]["target_id"] == "T_SECONDARY"
    assert rows[1]["direct_match_type"] == "SECONDARY"
    assert rows[1]["direct_match_accession"] == "Q22222"
    assert rows[1]["primary_accession"] == "X33333"
    assert rows[1]["frag"] == "1"
    assert rows[1]["raw_ox"] == "10090"

    summary = read_summary(tmp_path / "summary.tsv")

    assert summary["release"] == "SYNTHETIC"
    assert summary["target_count"] == "4"
    assert summary["targets_with_optional_accession"] == "3"
    assert summary["distinct_target_optional_accessions"] == "3"
    assert summary["full_v2_record_count"] == "2"
    assert summary["anchor_rows"] == "2"
    assert summary["primary_match_rows"] == "1"
    assert summary["secondary_match_rows"] == "1"
    assert summary["targets_with_direct_anchor"] == "2"
    assert summary["targets_without_direct_anchor"] == "1"


def test_duplicate_target_id_fails_closed(
    scanner_binary: Path,
    tmp_path: Path,
) -> None:
    result = run_scanner(
        scanner_binary,
        tmp_path,
        (
            "target_id\toptional_accession\n"
            "T1\tP11111\n"
            "T1\tP22222\n"
        ),
        (
            ">sp|P11111|ENTRY RID=1 SRC=sp REC=Reviewed OX=9606 "
            "FRAG=0 LEN=4 PE=1 SV=1 SEC=. GENE=.\n"
            "ACDE\n"
        ),
    )

    assert result.returncode == 2
    assert "duplicate target_id" in result.stderr


@pytest.mark.parametrize(
    "fasta",
    [
        "not-a-header\nACDE\n",
        (
            ">sp|P11111|ENTRY RID=1 SRC=sp REC=Reviewed OX=9606 "
            "FRAG=0 LEN=4 PE=1 SV=1 SEC=. GENE=.\n"
        ),
        (
            ">sp|P11111|ENTRY RID=1 SRC=sp REC=Reviewed OX=9606 "
            "LEN=4 PE=1 SV=1 SEC=. GENE=.\n"
            "ACDE\n"
        ),
    ],
)
def test_malformed_v2_fails_closed(
    scanner_binary: Path,
    tmp_path: Path,
    fasta: str,
) -> None:
    result = run_scanner(
        scanner_binary,
        tmp_path,
        "target_id\toptional_accession\nT1\tP11111\n",
        fasta,
    )

    assert result.returncode == 2
