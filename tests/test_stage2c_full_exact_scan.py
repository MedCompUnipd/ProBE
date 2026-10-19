"""Stage 2C3A FULL-V2 exact scanner synthetic contract tests."""

from __future__ import annotations

import csv
import shutil
import subprocess
from pathlib import Path

import pytest

SEQ = "ACDEFGHIKLMNPQRSTVWY"


@pytest.fixture(scope="module")
def scanner(tmp_path_factory: pytest.TempPathFactory) -> Path:
    compiler = (
        shutil.which("g++")
        or shutil.which("c++")
        or shutil.which("x86_64-conda-linux-gnu-c++")
    )
    if compiler is None:
        pytest.skip("C++20 compiler unavailable")
    root = Path(__file__).resolve().parents[1]
    source = root / "cpp/src/probe_full_exact_cross_strain_v1.cpp"
    executable = tmp_path_factory.mktemp("stage2c3_cpp") / "exact"
    subprocess.run(
        [compiler, "-std=c++20", "-O1", str(source), "-o", str(executable)],
        capture_output=True,
        text=True,
        check=True,
    )
    return executable


def run_scan(
    scanner: Path,
    root: Path,
    records: list[tuple[str, str, str]],
    *,
    target_taxid: str = "111",
    target_species: str = "562",
    target_sequence: str = SEQ,
    second_target: bool = False,
    ox_taxonomy: str | None = None,
) -> tuple[list[dict[str, str]], dict[str, str]]:
    root.mkdir(parents=True, exist_ok=True)
    target_lines = [
        "target_id\tnormalized_sequence",
        f"T_ARBITRARY\t{target_sequence}",
    ]
    taxonomy_lines = [
        "target_id\tresolved_taxid\tspecies_taxid\tprimary_bucket",
        f"T_ARBITRARY\t{target_taxid}\t{target_species}\tT:{target_taxid}",
    ]
    if second_target:
        target_lines.append(f"another-header\t{target_sequence}")
        taxonomy_lines.append("another-header\t111\t562\tT:111")
    (root / "targets.tsv").write_text("\n".join(target_lines) + "\n", encoding="utf-8")
    (root / "taxonomy.tsv").write_text(
        "\n".join(taxonomy_lines) + "\n", encoding="utf-8"
    )
    default_ox = (
        "raw_ox\tresolved_taxid\tspecies_taxid\tspecies_bucket\n"
        "111\t111\t562\tS:562\n"
        "112\t112\t562\tS:562\n"
        "999\t112\t562\tS:562\n"
        "9606\t9606\t9606\tS:9606\n"
    )
    (root / "ox.tsv").write_text(ox_taxonomy or default_ox, encoding="utf-8")
    fasta_lines = []
    for rid, (ox, fragment, sequence) in enumerate(records, start=1):
        fasta_lines.extend(
            [
                f">sp|P{rid:05d}|PROT OX={ox} FRAG={fragment} RID={rid} SRC=SP REC=R",
                sequence,
            ]
        )
    (root / "full.fasta").write_text("\n".join(fasta_lines) + "\n", encoding="utf-8")
    command = [
        str(scanner),
        "--targets",
        str(root / "targets.tsv"),
        "--target-taxonomy",
        str(root / "taxonomy.tsv"),
        "--ox-taxonomy",
        str(root / "ox.tsv"),
        "--fasta",
        str(root / "full.fasta"),
        "--output",
        str(root / "pairs.tsv"),
        "--summary",
        str(root / "summary.tsv"),
        "--release-id",
        "2023_01",
    ]
    subprocess.run(command, check=True, capture_output=True, text=True)
    with (root / "pairs.tsv").open(encoding="utf-8", newline="") as stream:
        pairs = list(csv.DictReader(stream, delimiter="\t"))
    with (root / "summary.tsv").open(encoding="utf-8", newline="") as stream:
        reader = csv.reader(stream, delimiter="\t")
        assert next(reader) == ["metric", "value"]
        summary = dict(reader)
    return pairs, summary


def test_same_taxid_literal_exact(scanner: Path, tmp_path: Path) -> None:
    pairs, summary = run_scan(scanner, tmp_path, [("111", "0", SEQ)])
    assert [row["rid"] for row in pairs] == ["1"]
    assert pairs[0]["candidate_resolved_taxid"] == "111"
    assert pairs[0]["target_resolved_taxid"] == "111"
    assert pairs[0]["exact_full_sequence"] == "1"
    assert summary["same_taxid_exact_pairs"] == "1"
    assert summary["cross_strain_exact_pairs"] == "0"
    assert summary["full_literal_exact_pairs"] == "1"


def test_other_strain_exact_match(scanner: Path, tmp_path: Path) -> None:
    pairs, summary = run_scan(scanner, tmp_path, [("112", "0", SEQ)])
    assert len(pairs) == 1
    assert pairs[0]["target_id"] == "T_ARBITRARY"
    assert pairs[0]["candidate_resolved_taxid"] == "112"
    assert pairs[0]["target_resolved_taxid"] == "111"
    assert pairs[0]["exact_full_sequence"] == "1"
    assert pairs[0]["discovery_channel"] == "FULL_V2_EXACT"
    assert summary["cross_strain_exact_pairs"] == "1"


@pytest.mark.parametrize(
    ("ox", "fragment", "seq"),
    [
        ("9606", "0", SEQ),
        ("112", "0", SEQ[:-1] + "A"),
        ("112", "0", SEQ[:-1]),
        ("112", "1", SEQ),
        ("112", "0", SEQ[:-1] + "X"),
        ("888888", "0", SEQ),
        ("112,111", "0", SEQ),
    ],
)
def test_reject_noneligible_cross_strain(
    scanner: Path,
    tmp_path: Path,
    ox: str,
    fragment: str,
    seq: str,
) -> None:
    pairs, _ = run_scan(scanner, tmp_path, [(ox, fragment, seq)])
    assert not pairs


def test_resolved_merged_taxid_and_duplicates(scanner: Path, tmp_path: Path) -> None:
    pairs, summary = run_scan(
        scanner, tmp_path, [("999", "0", SEQ)], second_target=True
    )
    assert {row["target_id"] for row in pairs} == {"T_ARBITRARY", "another-header"}
    assert summary["matched_full_records"] == "1"
    assert summary["cross_strain_exact_pairs"] == "2"


def test_short_exact_sequences_are_retained(scanner: Path, tmp_path: Path) -> None:
    pairs, _ = run_scan(scanner, tmp_path, [("112", "0", "ACD")], target_sequence="ACD")
    assert len(pairs) == 1
    assert pairs[0]["length_aa"] == "3"


def test_multiple_records_preserve_full_ordinal(scanner: Path, tmp_path: Path) -> None:
    pairs, summary = run_scan(
        scanner,
        tmp_path,
        [("111", "0", SEQ), ("112", "0", SEQ), ("999", "0", SEQ)],
    )
    assert [row["rid"] for row in pairs] == ["1", "2", "3"]
    assert [row["candidate_resolved_taxid"] for row in pairs] == [
        "111", "112", "112"
    ]
    assert summary["same_taxid_exact_pairs"] == "1"
    assert summary["cross_strain_exact_pairs"] == "2"
    assert summary["full_fasta_records"] == "3"


def test_output_cannot_be_overwritten(scanner: Path, tmp_path: Path) -> None:
    run_scan(scanner, tmp_path, [("112", "0", SEQ)])
    with pytest.raises(subprocess.CalledProcessError):
        run_scan(scanner, tmp_path, [("112", "0", SEQ)])


def test_unresolved_target_taxonomy(scanner: Path, tmp_path: Path) -> None:
    pairs, _ = run_scan(scanner, tmp_path, [("112", "0", SEQ)], target_taxid="")
    assert pairs == []


def test_unexpected_species_bucket_fails(scanner: Path, tmp_path: Path) -> None:
    wrong = (
        "raw_ox\tresolved_taxid\tspecies_taxid\tspecies_bucket\n112\t112\t562\tS:999\n"
    )
    with pytest.raises(subprocess.CalledProcessError):
        run_scan(scanner, tmp_path, [("112", "0", SEQ)], ox_taxonomy=wrong)
