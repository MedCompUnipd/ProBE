"""Species-wide preflight does not reject valid strain TaxIDs."""

from pathlib import Path

from probe.workflow.candidate_scan import preflight_candidate_scan


def test_species_wide_accepts_strain_with_no_primary_bucket(
    tmp_path: Path,
) -> None:
    targets = tmp_path / "targets.tsv"
    taxonomy = tmp_path / "taxonomy.tsv"
    targets.write_text(
        "target_id\tnormalized_sequence\nT1\tACDE\n",
        encoding="utf-8",
    )
    taxonomy.write_text(
        "target_id\tresolved_taxid\tspecies_taxid\tprimary_bucket\nT1\t111\t562\t\n",
        encoding="utf-8",
    )
    broad = preflight_candidate_scan(targets, taxonomy, taxonomy_policy="species_wide")
    restricted = preflight_candidate_scan(
        targets, taxonomy, taxonomy_policy="rank_aware"
    )
    assert broad.eligible_target_count == 1
    assert restricted.eligible_target_count == 0


def test_species_wide_rejects_unresolved_taxonomy(tmp_path: Path) -> None:
    targets = tmp_path / "targets.tsv"
    taxonomy = tmp_path / "taxonomy.tsv"
    targets.write_text(
        "target_id\tnormalized_sequence\nT1\tACDE\n",
        encoding="utf-8",
    )
    taxonomy.write_text(
        "target_id\tresolved_taxid\tspecies_taxid\tprimary_bucket\nT1\t111\t\tT:111\n",
        encoding="utf-8",
    )
    result = preflight_candidate_scan(targets, taxonomy, taxonomy_policy="species_wide")
    assert result.eligible_target_count == 0
    assert result.unresolved_target_count == 1
