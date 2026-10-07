from pathlib import Path

import pytest

from probe.workflow.candidate_scan import (
    PAIR_COLUMNS,
    CandidateScanPreflight,
    preflight_candidate_scan,
    write_empty_candidate_scan_outputs,
)

TARGET_HEADER = "target_id\tnormalized_sequence\n"
TAXONOMY_HEADER = "target_id\tresolved_taxid\tspecies_taxid\tprimary_bucket\n"


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_preflight_accepts_zero_targets(tmp_path):
    targets = write(tmp_path / "targets.tsv", TARGET_HEADER)
    taxonomy = write(tmp_path / "taxonomy.tsv", TAXONOMY_HEADER)

    result = preflight_candidate_scan(
        targets,
        taxonomy,
        taxonomy_policy="rank_aware",
    )

    assert result == CandidateScanPreflight(
        target_count=0,
        taxonomy_count=0,
        eligible_target_count=0,
        unresolved_target_count=0,
        empty_primary_bucket_count=0,
    )


def test_preflight_classifies_rank_aware_eligibility(tmp_path):
    targets = write(
        tmp_path / "targets.tsv",
        TARGET_HEADER + "T1\tACDEFG\n" + "T2\tMNPQRST\n" + "T3\tAAAAAAA\n",
    )
    taxonomy = write(
        tmp_path / "taxonomy.tsv",
        TAXONOMY_HEADER
        + "T1\t9606\t9606\tS:9606\n"
        + "T2\t\t\t\n"
        + "T3\t9606\t9606\t\n",
    )

    result = preflight_candidate_scan(
        targets,
        taxonomy,
        taxonomy_policy="rank_aware",
    )

    assert result == CandidateScanPreflight(
        target_count=3,
        taxonomy_count=3,
        eligible_target_count=1,
        unresolved_target_count=1,
        empty_primary_bucket_count=1,
    )


def test_strict_taxon_does_not_require_primary_bucket(tmp_path):
    targets = write(
        tmp_path / "targets.tsv",
        TARGET_HEADER + "T1\tACDEFG\n",
    )
    taxonomy = write(
        tmp_path / "taxonomy.tsv",
        TAXONOMY_HEADER + "T1\t9606\t9606\t\n",
    )

    result = preflight_candidate_scan(
        targets,
        taxonomy,
        taxonomy_policy="strict_taxon",
    )

    assert result.eligible_target_count == 1
    assert result.empty_primary_bucket_count == 0


@pytest.mark.parametrize(
    ("target_rows", "taxonomy_rows", "match"),
    [
        (
            "T1\tAAAAAAA\nT1\tBBBBBBB\n",
            "T1\t9606\t9606\tS:9606\n",
            "duplicate target_id",
        ),
        (
            "T1\tAAAAAAA\n",
            ("T1\t9606\t9606\tS:9606\nT1\t9606\t9606\tS:9606\n"),
            "duplicate target_id",
        ),
        (
            "T1\tAAAAAAA\n",
            "",
            "missing target_ids",
        ),
        (
            "",
            "T1\t9606\t9606\tS:9606\n",
            "unexpected target_ids",
        ),
    ],
)
def test_preflight_rejects_contract_violations(
    tmp_path,
    target_rows,
    taxonomy_rows,
    match,
):
    targets = write(
        tmp_path / "targets.tsv",
        TARGET_HEADER + target_rows,
    )
    taxonomy = write(
        tmp_path / "taxonomy.tsv",
        TAXONOMY_HEADER + taxonomy_rows,
    )

    with pytest.raises(ValueError, match=match):
        preflight_candidate_scan(
            targets,
            taxonomy,
            taxonomy_policy="rank_aware",
        )


def test_empty_outputs_match_scanner_schema(tmp_path):
    prefix = tmp_path / "scan"

    write_empty_candidate_scan_outputs(
        prefix,
        snapshot="TEST-SNAPSHOT",
        policy={
            "k": 7,
            "max_required_shared": 10,
            "min_target_kmer_fraction": 0.25,
            "fragment_policy": "exclude",
            "taxonomy_policy": "rank_aware",
        },
    )

    pairs = Path(f"{prefix}.pairs.tsv")
    unsearchable = Path(f"{prefix}.unsearchable_targets.tsv")
    summary = Path(f"{prefix}.summary.tsv")

    assert pairs.read_text(encoding="utf-8") == ("\t".join(PAIR_COLUMNS) + "\n")
    assert len(pairs.read_text(encoding="utf-8").rstrip("\n").split("\t")) == 35

    assert unsearchable.read_text(encoding="utf-8") == ("target_id\tlength\treason\n")

    summary_text = summary.read_text(encoding="utf-8")
    assert "snapshot\tTEST-SNAPSHOT\n" in summary_text
    assert "targets_loaded\t0\n" in summary_text
    assert "targets_unsearchable_len_lt_k\t0\n" in summary_text
    assert "pairs_emitted\t0\n" in summary_text
    assert "max_pairs_for_one_record\t0\n" in summary_text


def test_preflight_does_not_treat_sequence_shorter_than_k_as_ineligible(
    tmp_path,
):
    targets = write(
        tmp_path / "targets.tsv",
        TARGET_HEADER + "T1\tACD\n",
    )
    taxonomy = write(
        tmp_path / "taxonomy.tsv",
        TAXONOMY_HEADER + "T1\t9606\t9606\tS:9606\n",
    )

    result = preflight_candidate_scan(
        targets,
        taxonomy,
        taxonomy_policy="rank_aware",
    )

    assert result.target_count == 1
    assert result.eligible_target_count == 1
    assert result.unresolved_target_count == 0
    assert result.empty_primary_bucket_count == 0
