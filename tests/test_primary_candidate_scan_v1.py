import csv
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUILD_DIR = ROOT / ".pixi/build/probe-primary-candidate-scan"
BINARY_NAME = (
    "probe-primary-candidate-scan-v1.exe"
    if os.name == "nt"
    else "probe-primary-candidate-scan-v1"
)
BINARY = BUILD_DIR / BINARY_NAME


def _pixi_executable() -> str:
    configured = os.environ.get("PIXI_EXE")
    if configured:
        return configured

    executable = shutil.which("pixi")
    if executable is None:
        pytest.skip("pixi executable is unavailable")

    return executable


@pytest.fixture(scope="session")
def scanner_binary() -> Path:
    subprocess.run(
        [_pixi_executable(), "run", "build-primary-candidate-scan"],
        cwd=ROOT,
        check=True,
    )

    if not BINARY.is_file():
        raise AssertionError(f"expected native binary: {BINARY}")

    return BINARY


def _write_inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    targets = tmp_path / "targets.tsv"
    targets.write_text(
        "target_id\tnormalized_sequence\n"
        "T_SPECIES\tABCDEFGHIJ\n"
        "T_STRAIN\tKLMNOPQRST\n"
        "T_SHORT\tABCDEF\n",
        encoding="utf-8",
    )

    target_taxonomy = tmp_path / "target_taxonomy.tsv"
    target_taxonomy.write_text(
        "target_id\traw_taxid\tresolution_status\tresolved_taxid\t"
        "resolved_rank\tmerge_path\tlineage_status\tspecies_status\t"
        "species_taxid\tprimary_bucket\n"
        "T_SPECIES\t9606\tCURRENT\t9606\tspecies\t\tRESOLVED\t"
        "RESOLVED\t9606\tS:9606\n"
        "T_STRAIN\t12345\tCURRENT\t12345\tstrain\t\tRESOLVED\t"
        "RESOLVED\t9606\tT:12345\n"
        "T_SHORT\t9606\tCURRENT\t9606\tspecies\t\tRESOLVED\t"
        "RESOLVED\t9606\tS:9606\n",
        encoding="utf-8",
    )

    ox_taxonomy = tmp_path / "ox_taxonomy.tsv"
    ox_taxonomy.write_text(
        "raw_ox\tresolution_status\tresolved_taxid\tresolved_rank\t"
        "merge_path\tlineage_status\tspecies_status\tspecies_taxid\t"
        "species_bucket\tresolved_bucket\n"
        "12345\tCURRENT\t12345\tstrain\t\tRESOLVED\tRESOLVED\t"
        "9606\tS:9606\tT:12345\n"
        "9606\tCURRENT\t9606\tspecies\t\tRESOLVED\tRESOLVED\t"
        "9606\tS:9606\tT:9606\n"
        "9999\tUNRESOLVED\t\t\t\tUNRESOLVED\tUNRESOLVED\t\t\t\n",
        encoding="utf-8",
    )

    return targets, target_taxonomy, ox_taxonomy


def _run(
    binary: Path,
    targets: Path,
    target_taxonomy: Path,
    ox_taxonomy: Path,
    fasta: Path,
    prefix: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(binary),
            "--targets",
            str(targets),
            "--target-taxonomy",
            str(target_taxonomy),
            "--ox-taxonomy",
            str(ox_taxonomy),
            "--fasta",
            str(fasta),
            "--snapshot",
            "SYNTHETIC",
            "--out-prefix",
            str(prefix),
        ],
        text=True,
        capture_output=True,
        check=False,
    )


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _read_summary(path: Path) -> dict[str, str]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = csv.reader(handle, delimiter="\t")
        header = next(rows)
        assert header == ["metric", "value"]
        return {key: value for key, value in rows}


def test_primary_candidate_semantics(
    scanner_binary: Path,
    tmp_path: Path,
) -> None:
    targets, target_taxonomy, ox_taxonomy = _write_inputs(tmp_path)

    fasta = tmp_path / "go_positive.fasta"
    fasta.write_text(
        ">sp|P_SPEC|SPEC RID=1 SRC=sp REC=Reviewed OX=9606 "
        "FRAG=0 PE=1 SV=1 FLAGS=. SEC=. GENE=.\n"
        "ABCDEFGHIJ\n"
        ">sp|P_PARTIAL|PARTIAL RID=2 SRC=sp REC=Reviewed OX=9606 "
        "FRAG=0 PE=1 SV=1 FLAGS=. SEC=. GENE=.\n"
        "ABCDEFGXYZ\n"
        ">tr|P_SUB|SUB RID=3 SRC=tr REC=Unreviewed OX=12345 "
        "FRAG=0 PE=2 SV=1 FLAGS=. SEC=. GENE=.\n"
        "KLMNOPQRST\n"
        ">sp|P_SUB_WRONG|SUBWRONG RID=4 SRC=sp REC=Reviewed OX=9606 "
        "FRAG=0 PE=1 SV=1 FLAGS=. SEC=. GENE=.\n"
        "KLMNOPQRST\n"
        ">unk|P_SPEC_SUB|SPECSUB RID=5 SRC=unk REC=Unknown OX=12345 "
        "FRAG=0 PE=3 SV=1 FLAGS=. SEC=. GENE=.\n"
        "ABCDEFGHIJ\n"
        ">sp|P_FRAG|FRAG RID=6 SRC=sp REC=Reviewed OX=9606 "
        "FRAG=1 PE=1 SV=1 FLAGS=. SEC=. GENE=.\n"
        "ABCDEFGHIJ\n"
        ">sp|P_NOPRIMARY|NOPRIMARY RID=7 SRC=sp REC=Reviewed OX=9999 "
        "FRAG=0 PE=1 SV=1 FLAGS=. SEC=. GENE=.\n"
        "ABCDEFGHIJ\n"
        ">sp|P_MISSING|MISSING RID=8 SRC=sp REC=Reviewed "
        "FRAG=0 PE=1 SV=1 FLAGS=. SEC=. GENE=.\n"
        "ABCDEFGHIJ\n"
        ">sp|P_MAL|MAL RID=9 SRC=sp REC=Reviewed OX=NA "
        "FRAG=0 PE=1 SV=1 FLAGS=. SEC=. GENE=.\n"
        "ABCDEFGHIJ\n"
        ">sp|P_SHORT|SHORT RID=10 SRC=sp REC=Reviewed OX=9606 "
        "FRAG=0 PE=1 SV=1 FLAGS=. SEC=. GENE=.\n"
        "ABCDEF\n",
        encoding="utf-8",
    )

    prefix = tmp_path / "stage03"

    result = _run(
        scanner_binary,
        targets,
        target_taxonomy,
        ox_taxonomy,
        fasta,
        prefix,
    )

    assert result.returncode == 0, result.stderr

    pairs = _read_tsv(Path(f"{prefix}.pairs.tsv"))
    assert len(pairs) == 3

    observed = {
        (
            row["record_number"],
            row["accession"],
            row["target_id"],
            row["target_bucket"],
            row["exact_sequence"],
        )
        for row in pairs
    }

    assert observed == {
        ("1", "P_SPEC", "T_SPECIES", "S:9606", "1"),
        ("3", "P_SUB", "T_STRAIN", "T:12345", "1"),
        ("5", "P_SPEC_SUB", "T_SPECIES", "S:9606", "1"),
    }

    accessions = {row["accession"] for row in pairs}
    assert "P_PARTIAL" not in accessions
    assert "P_SUB_WRONG" not in accessions
    assert "P_FRAG" not in accessions

    unsearchable = _read_tsv(
        Path(f"{prefix}.unsearchable_targets.tsv")
    )
    assert unsearchable == [
        {
            "target_id": "T_SHORT",
            "length": "6",
            "reason": "LEN_LT_K",
        }
    ]

    summary = _read_summary(Path(f"{prefix}.summary.tsv"))

    expected = {
        "snapshot": "SYNTHETIC",
        "k": "7",
        "max_required_shared": "10",
        "min_target_kmer_fraction": "0.25",
        "fragment_policy": "exclude",
        "taxonomy_policy": "rank_aware",
        "targets_loaded": "3",
        "targets_unsearchable_len_lt_k": "1",
        "fasta_records_total": "10",
        "records_missing_ox": "1",
        "records_malformed_ox": "1",
        "records_ox_not_in_taxonomy_map": "0",
        "records_no_primary_bucket": "1",
        "records_fragment_skipped": "1",
        "records_primary_eligible": "7",
        "records_primary_len_lt_k": "1",
        "records_with_emitted_pair": "3",
        "pairs_emitted": "3",
        "exact_sequence_pairs": "3",
        "pairs_positional_seed_zero": "0",
        "pairs_positional_seed_ge1": "3",
        "pairs_positional_seed_ge5": "0",
        "pairs_positional_seed_ge10": "0",
        "pairs_bidir_span_ge_0.25": "3",
        "pairs_bidir_span_ge_0.50": "3",
        "pairs_bidir_span_ge_0.75": "3",
        "pairs_bidir_span_ge_0.90": "3",
        "pairs_bidir_span_ge_0.95": "3",
        "pairs_bidir_seed_cov_ge_0.25": "3",
        "pairs_bidir_seed_cov_ge_0.50": "3",
        "pairs_bidir_seed_cov_ge_0.75": "3",
        "pairs_bidir_seed_cov_ge_0.90": "3",
        "pairs_lis_fraction_ge_0.50": "3",
        "pairs_lis_fraction_ge_0.75": "3",
        "pairs_lis_fraction_ge_0.90": "3",
        "pairs_lis_fraction_ge_0.95": "3",
        "pairs_length_ratio_ge_0.50": "3",
        "pairs_length_ratio_ge_0.75": "3",
        "pairs_length_ratio_ge_0.90": "3",
        "pairs_length_ratio_ge_0.95": "3",
        "max_pairs_for_one_record": "1",
    }

    assert summary == expected


def test_invalid_fragment_token_fails_closed(
    scanner_binary: Path,
    tmp_path: Path,
) -> None:
    targets, target_taxonomy, ox_taxonomy = _write_inputs(tmp_path)

    fasta = tmp_path / "invalid_frag.fasta"
    fasta.write_text(
        ">sp|P_BAD|BAD RID=1 SRC=sp REC=Reviewed OX=9606 "
        "FRAG=2 PE=1 SV=1 FLAGS=. SEC=. GENE=.\n"
        "ABCDEFGHIJ\n",
        encoding="utf-8",
    )

    prefix = tmp_path / "stage03"

    result = _run(
        scanner_binary,
        targets,
        target_taxonomy,
        ox_taxonomy,
        fasta,
        prefix,
    )

    assert result.returncode == 2
    assert "invalid FRAG token: 2" in result.stderr

    assert not Path(f"{prefix}.pairs.tsv").exists()
    assert not Path(f"{prefix}.summary.tsv").exists()
    assert not Path(f"{prefix}.unsearchable_targets.tsv").exists()



def test_cli_help_documents_scientific_parameters(
    scanner_binary: Path,
) -> None:
    result = subprocess.run(
        [str(scanner_binary), "--help"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "--k INTEGER" in result.stdout
    assert "Allowed range: 4..8" in result.stdout
    assert "--max-required-shared INTEGER" in result.stdout
    assert "Allowed range: 1..65535" in result.stdout
    assert "--min-target-kmer-fraction FLOAT" in result.stdout
    assert "0.20..1.00" in result.stdout
    assert "NOT sequence percentage identity" in result.stdout
    assert "--fragment-policy {exclude,include}" in result.stdout
    assert "--taxonomy-policy {rank_aware,strict_taxon}" in result.stdout


def test_runtime_sequence_policy_overrides(
    scanner_binary: Path,
    tmp_path: Path,
) -> None:
    targets = tmp_path / "targets.tsv"
    targets.write_text(
        "target_id\tnormalized_sequence\n"
        "T_SHORT\tABCDEF\n",
        encoding="utf-8",
    )

    target_taxonomy = tmp_path / "target_taxonomy.tsv"
    target_taxonomy.write_text(
        "target_id\traw_taxid\tresolution_status\tresolved_taxid\t"
        "resolved_rank\tmerge_path\tlineage_status\tspecies_status\t"
        "species_taxid\tprimary_bucket\n"
        "T_SHORT\t9606\tCURRENT\t9606\tspecies\t\tRESOLVED\t"
        "RESOLVED\t9606\tS:9606\n",
        encoding="utf-8",
    )

    ox_taxonomy = tmp_path / "ox_taxonomy.tsv"
    ox_taxonomy.write_text(
        "raw_ox\tresolution_status\tresolved_taxid\tresolved_rank\t"
        "merge_path\tlineage_status\tspecies_status\tspecies_taxid\t"
        "species_bucket\tresolved_bucket\n"
        "9606\tCURRENT\t9606\tspecies\t\tRESOLVED\tRESOLVED\t"
        "9606\tS:9606\tT:9606\n",
        encoding="utf-8",
    )

    fasta = tmp_path / "go_positive.fasta"
    fasta.write_text(
        ">sp|P_SHORT|SHORT RID=1 SRC=sp REC=Reviewed OX=9606 "
        "FRAG=1 PE=1 SV=1 FLAGS=. SEC=. GENE=.\n"
        "ABCDEF\n",
        encoding="utf-8",
    )

    prefix = tmp_path / "stage03"

    result = subprocess.run(
        [
            str(scanner_binary),
            "--targets",
            str(targets),
            "--target-taxonomy",
            str(target_taxonomy),
            "--ox-taxonomy",
            str(ox_taxonomy),
            "--fasta",
            str(fasta),
            "--snapshot",
            "SYNTHETIC",
            "--out-prefix",
            str(prefix),
            "--k",
            "4",
            "--max-required-shared",
            "2",
            "--min-target-kmer-fraction",
            "0.5",
            "--fragment-policy",
            "include",
            "--taxonomy-policy",
            "rank_aware",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr

    pairs = _read_tsv(Path(f"{prefix}.pairs.tsv"))
    assert len(pairs) == 1

    row = pairs[0]
    assert row["target_id"] == "T_SHORT"
    assert row["accession"] == "P_SHORT"
    assert row["target_distinct_kmers"] == "3"
    assert row["shared_distinct_kmers"] == "3"
    assert row["exact_sequence"] == "1"
    assert row["frag"] == "1"

    summary = _read_summary(Path(f"{prefix}.summary.tsv"))
    assert summary["k"] == "4"
    assert summary["max_required_shared"] == "2"
    assert summary["min_target_kmer_fraction"] == "0.5"
    assert summary["fragment_policy"] == "include"
    assert summary["taxonomy_policy"] == "rank_aware"
    assert summary["targets_unsearchable_len_lt_k"] == "0"
    assert summary["records_fragment_skipped"] == "0"
    assert summary["pairs_emitted"] == "1"


def test_strict_taxon_rejects_same_species_subtaxon_match(
    scanner_binary: Path,
    tmp_path: Path,
) -> None:
    targets = tmp_path / "targets.tsv"
    targets.write_text(
        "target_id\tnormalized_sequence\n"
        "T_SPECIES\tABCDEFGHIJ\n",
        encoding="utf-8",
    )

    target_taxonomy = tmp_path / "target_taxonomy.tsv"
    target_taxonomy.write_text(
        "target_id\traw_taxid\tresolution_status\tresolved_taxid\t"
        "resolved_rank\tmerge_path\tlineage_status\tspecies_status\t"
        "species_taxid\tprimary_bucket\n"
        "T_SPECIES\t9606\tCURRENT\t9606\tspecies\t\tRESOLVED\t"
        "RESOLVED\t9606\tS:9606\n",
        encoding="utf-8",
    )

    ox_taxonomy = tmp_path / "ox_taxonomy.tsv"
    ox_taxonomy.write_text(
        "raw_ox\tresolution_status\tresolved_taxid\tresolved_rank\t"
        "merge_path\tlineage_status\tspecies_status\tspecies_taxid\t"
        "species_bucket\tresolved_bucket\n"
        "12345\tCURRENT\t12345\tstrain\t\tRESOLVED\tRESOLVED\t"
        "9606\tS:9606\tT:12345\n"
        "9606\tCURRENT\t9606\tspecies\t\tRESOLVED\tRESOLVED\t"
        "9606\tS:9606\tT:9606\n",
        encoding="utf-8",
    )

    fasta = tmp_path / "go_positive.fasta"
    fasta.write_text(
        ">sp|P_SPECIES|SPECIES RID=1 SRC=sp REC=Reviewed OX=9606 "
        "FRAG=0 PE=1 SV=1 FLAGS=. SEC=. GENE=.\n"
        "ABCDEFGHIJ\n"
        ">sp|P_STRAIN|STRAIN RID=2 SRC=sp REC=Reviewed OX=12345 "
        "FRAG=0 PE=1 SV=1 FLAGS=. SEC=. GENE=.\n"
        "ABCDEFGHIJ\n",
        encoding="utf-8",
    )

    prefix = tmp_path / "stage03"

    result = subprocess.run(
        [
            str(scanner_binary),
            "--targets",
            str(targets),
            "--target-taxonomy",
            str(target_taxonomy),
            "--ox-taxonomy",
            str(ox_taxonomy),
            "--fasta",
            str(fasta),
            "--snapshot",
            "SYNTHETIC",
            "--out-prefix",
            str(prefix),
            "--taxonomy-policy",
            "strict_taxon",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr

    pairs = _read_tsv(Path(f"{prefix}.pairs.tsv"))

    assert [
        (row["accession"], row["target_id"], row["target_bucket"])
        for row in pairs
    ] == [
        ("P_SPECIES", "T_SPECIES", "T:9606"),
    ]

    summary = _read_summary(Path(f"{prefix}.summary.tsv"))
    assert summary["taxonomy_policy"] == "strict_taxon"
    assert summary["pairs_emitted"] == "1"


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (
            ["--k", "3"],
            "--k must be in the range 4..8",
        ),
        (
            ["--k", "9"],
            "--k must be in the range 4..8",
        ),
        (
            ["--max-required-shared", "0"],
            "--max-required-shared must be in the range 1..65535",
        ),
        (
            ["--max-required-shared", "65536"],
            "--max-required-shared must be in the range 1..65535",
        ),
        (
            ["--min-target-kmer-fraction", "0.19"],
            "--min-target-kmer-fraction must be in the range 0.20..1.00",
        ),
        (
            ["--min-target-kmer-fraction", "1.01"],
            "--min-target-kmer-fraction must be in the range 0.20..1.00",
        ),
        (
            ["--min-target-kmer-fraction", "nan"],
            "--min-target-kmer-fraction requires a finite",
        ),
        (
            ["--fragment-policy", "sometimes"],
            "--fragment-policy must be exclude or include",
        ),
        (
            ["--taxonomy-policy", "permissive"],
            "--taxonomy-policy must be rank_aware or strict_taxon",
        ),
    ],
)
def test_invalid_runtime_policy_fails_closed(
    scanner_binary: Path,
    tmp_path: Path,
    arguments: list[str],
    message: str,
) -> None:
    targets, target_taxonomy, ox_taxonomy = _write_inputs(tmp_path)

    fasta = tmp_path / "go_positive.fasta"
    fasta.write_text(
        ">sp|P1|ONE RID=1 SRC=sp REC=Reviewed OX=9606 "
        "FRAG=0 PE=1 SV=1 FLAGS=. SEC=. GENE=.\n"
        "ABCDEFGHIJ\n",
        encoding="utf-8",
    )

    prefix = tmp_path / "stage03"

    result = subprocess.run(
        [
            str(scanner_binary),
            "--targets",
            str(targets),
            "--target-taxonomy",
            str(target_taxonomy),
            "--ox-taxonomy",
            str(ox_taxonomy),
            "--fasta",
            str(fasta),
            "--snapshot",
            "SYNTHETIC",
            "--out-prefix",
            str(prefix),
            *arguments,
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert message in result.stderr


@pytest.mark.parametrize(
    "arguments",
    [
        ["--k", "4"],
        ["--k", "8"],
        ["--max-required-shared", "1"],
        ["--max-required-shared", "65535"],
        ["--min-target-kmer-fraction", "0.20"],
        ["--min-target-kmer-fraction", "1.00"],
    ],
)
def test_runtime_policy_inclusive_boundaries_are_accepted(
    scanner_binary: Path,
    tmp_path: Path,
    arguments: list[str],
) -> None:
    targets, target_taxonomy, ox_taxonomy = _write_inputs(tmp_path)

    fasta = tmp_path / "go_positive.fasta"
    fasta.write_text(
        ">sp|P_BOUNDARY|BOUNDARY RID=1 SRC=sp REC=Reviewed OX=9606 "
        "FRAG=0 PE=1 SV=1 FLAGS=. SEC=. GENE=.\n"
        "ABCDEFGHIJ\n",
        encoding="utf-8",
    )

    prefix = tmp_path / "stage03"

    result = subprocess.run(
        [
            str(scanner_binary),
            "--targets",
            str(targets),
            "--target-taxonomy",
            str(target_taxonomy),
            "--ox-taxonomy",
            str(ox_taxonomy),
            "--fasta",
            str(fasta),
            "--snapshot",
            "SYNTHETIC",
            "--out-prefix",
            str(prefix),
            *arguments,
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr

    pairs = _read_tsv(Path(f"{prefix}.pairs.tsv"))
    assert any(
        row["target_id"] == "T_SPECIES"
        and row["accession"] == "P_BOUNDARY"
        for row in pairs
    )
