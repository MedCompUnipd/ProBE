"""Tiny, real C++ scanner tests; no release FASTA or GOA is accessed."""

from __future__ import annotations

import csv
import shutil
import subprocess
from pathlib import Path

import pytest

SEQUENCE = "ACDEFGHIKLMNPQRSTVWYACDEFGHIKL"


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
    source = root / "cpp/src/probe_primary_candidate_scan_v2.cpp"
    executable = tmp_path_factory.mktemp("stage2c_cpp") / "scan"
    subprocess.run(
        [compiler, "-std=c++20", "-O1", str(source), "-o", str(executable)],
        check=True,
        capture_output=True,
        text=True,
    )
    return executable


def _run(
    scanner: Path,
    tmp_path: Path,
    policy: str | None,
    *,
    ox: str = "112",
    frag: str = "0",
    sequence: str = SEQUENCE,
) -> list[dict[str, str]]:
    tmp_path.mkdir(exist_ok=True, parents=True)
    (tmp_path / "targets.tsv").write_text(
        f"target_id\tnormalized_sequence\nT_A\t{SEQUENCE}\n",
        encoding="utf-8",
    )
    (tmp_path / "target_tax.tsv").write_text(
        "target_id\tresolved_taxid\tspecies_taxid\tprimary_bucket\n"
        "T_A\t111\t562\tT:111\n",
        encoding="utf-8",
    )
    (tmp_path / "ox_tax.tsv").write_text(
        "raw_ox\tresolved_taxid\tspecies_taxid\t"
        "species_bucket\tresolved_bucket\n"
        "111\t111\t562\tS:562\tT:111\n"
        "112\t112\t562\tS:562\tT:112\n"
        "999\t112\t562\tS:562\tT:112\n"
        "9606\t9606\t9606\tS:9606\tT:9606\n",
        encoding="utf-8",
    )
    (tmp_path / "candidates.fasta").write_text(
        f">sp|P00001|TEST OX={ox} FRAG={frag} RID=5 SRC=SP REC=R\n{sequence}\n",
        encoding="utf-8",
    )
    prefix = tmp_path / "output"
    command = [
        str(scanner),
        "--targets",
        str(tmp_path / "targets.tsv"),
        "--target-taxonomy",
        str(tmp_path / "target_tax.tsv"),
        "--ox-taxonomy",
        str(tmp_path / "ox_tax.tsv"),
        "--fasta",
        str(tmp_path / "candidates.fasta"),
        "--snapshot",
        "2023_01",
        "--out-prefix",
        str(prefix),
        "--max-required-shared",
        "1",
    ]
    if policy is not None:
        command.extend(["--taxonomy-policy", policy])
    subprocess.run(command, check=True, capture_output=True, text=True)
    with Path(f"{prefix}.pairs.tsv").open(encoding="utf-8") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def test_v2_default_finds_other_strain(scanner: Path, tmp_path: Path) -> None:
    rows = _run(scanner, tmp_path, None)
    assert len(rows) == 1
    assert rows[0]["exact_sequence"] == "1"
    assert rows[0]["target_bucket"] == "S:562"
    assert rows[0]["resolved_taxid"] == "112"


@pytest.mark.parametrize("policy", ["rank_aware", "strict_taxon"])
def test_legacy_scope_excludes_other_strain(
    scanner: Path, tmp_path: Path, policy: str
) -> None:
    assert _run(scanner, tmp_path, policy) == []


def test_species_wide_preserves_same_taxid(scanner: Path, tmp_path: Path) -> None:
    rows = _run(scanner, tmp_path, "species_wide", ox="111")
    assert len(rows) == 1
    assert rows[0]["resolved_taxid"] == "111"


def test_species_wide_never_admits_other_species(scanner: Path, tmp_path: Path) -> None:
    assert _run(scanner, tmp_path, "species_wide", ox="9606") == []


def test_species_wide_discovers_nonidentical_candidate(
    scanner: Path, tmp_path: Path
) -> None:
    altered = SEQUENCE[:-1] + "A"
    rows = _run(scanner, tmp_path, "species_wide", sequence=altered)
    assert len(rows) == 1
    assert rows[0]["exact_sequence"] == "0"


def test_fragment_not_added_to_similarity_discovery(
    scanner: Path, tmp_path: Path
) -> None:
    assert _run(scanner, tmp_path, "species_wide", frag="1") == []


def test_invalid_policy_fails_before_search(scanner: Path, tmp_path: Path) -> None:
    command = [str(scanner), "--taxonomy-policy", "invalid"]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode != 0
    assert "unknown" not in result.stderr.lower()


def test_merged_ox_uses_resolved_species(scanner: Path, tmp_path: Path) -> None:
    rows = _run(scanner, tmp_path, "species_wide", ox="999")
    assert len(rows) == 1
    assert rows[0]["raw_ox"] == "999"
    assert rows[0]["resolved_taxid"] == "112"


def test_v2_legacy_rank_aware_matches_frozen_v1(scanner: Path, tmp_path: Path) -> None:
    compiler = (
        shutil.which("g++")
        or shutil.which("c++")
        or shutil.which("x86_64-conda-linux-gnu-c++")
    )
    assert compiler is not None
    root = Path(__file__).resolve().parents[1]
    source = root / "cpp/src/probe_primary_candidate_scan_v1.cpp"
    legacy = tmp_path / "scanner-v1"
    subprocess.run(
        [compiler, "-std=c++20", "-O1", str(source), "-o", str(legacy)],
        check=True,
        capture_output=True,
        text=True,
    )
    old = _run(legacy, tmp_path / "legacy", "rank_aware", ox="111")
    new = _run(scanner, tmp_path / "new", "rank_aware", ox="111")
    assert new == old
