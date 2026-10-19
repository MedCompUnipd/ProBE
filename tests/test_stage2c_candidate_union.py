"""Read-only-scanner-input contract for species-wide discovery union."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from probe.stage2c_candidate_union import merge_discovery

GO_HEADERS = (
    "target_id",
    "rid",
    "accession",
    "raw_ox",
    "resolved_taxid",
    "species_taxid",
    "frag",
    "exact_sequence",
    "record_number",
)
FULL_HEADERS = (
    "release",
    "target_id",
    "record_number",
    "rid",
    "accession",
    "raw_ox",
    "target_resolved_taxid",
    "candidate_resolved_taxid",
    "species_taxid",
    "frag",
    "length_aa",
    "exact_full_sequence",
    "discovery_channel",
)


def _write(path: Path, header: tuple[str, ...], rows: list[tuple]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(header)
        writer.writerows(rows)


def _inputs(tmp_path: Path):
    tax = tmp_path / "tax.tsv"
    go = tmp_path / "go.tsv"
    full = tmp_path / "full.tsv"
    _write(
        tax,
        ("target_id", "resolved_taxid", "species_taxid"),
        [("T1", "111", "100"), ("T2", "300", "300")],
    )
    _write(
        go,
        GO_HEADERS,
        [
            ("T1", "21", "A21", "112", "112", "100", "0", "0", "1"),
            ("T1", "22", "A22", "111", "111", "100", "0", "0", "2"),
            ("T2", "31", "A31", "300", "300", "300", "0", "1", "3"),
            ("T1", "25", "A25", "113", "113", "100", "0", "1", "4"),
        ],
    )
    _write(
        full,
        FULL_HEADERS,
        [
            (
                "R1",
                "T1",
                "44",
                "24",
                "A24",
                "113",
                "111",
                "113",
                "100",
                "0",
                "20",
                "1",
                "FULL_V2_EXACT",
            ),
            (
                "R1",
                "T1",
                "55",
                "25",
                "A25",
                "113",
                "111",
                "113",
                "100",
                "0",
                "20",
                "1",
                "FULL_V2_EXACT",
            ),
        ],
    )
    return tax, go, full


def _merge(tmp_path: Path, paths):
    tax, go, full = paths
    return merge_discovery(
        release="R1",
        target_taxonomy=tax,
        go_positive_pairs=go,
        full_exact_pairs=full,
        output=tmp_path / "merged.tsv",
        summary=tmp_path / "summary.json",
    )


def test_general_taxonomy_and_donor_gates(tmp_path: Path) -> None:
    stats = _merge(tmp_path, _inputs(tmp_path))
    assert stats["unique_pairs"] == 5
    assert stats["both_channels"] == 1
    with (tmp_path / "merged.tsv").open() as handle:
        rows = {row["rid"]: row for row in csv.DictReader(handle, delimiter="\t")}
    assert rows["21"]["same_species_exact_gate"] == "DENIED"
    assert rows["22"]["strict_gate"] == "DEFER_TO_DOCUMENTARY"
    assert rows["24"]["same_species_exact_gate"] == "ELIGIBLE"
    assert rows["24"]["strict_gate"] == "DENIED"
    assert rows["25"]["full_exact_species_wide"] == "1"
    assert rows["25"]["go_positive_species_wide"] == "1"
    assert rows["31"]["strict_gate"] == "DEFER_TO_DOCUMENTARY"


def test_duplicate_go_pair_refused(tmp_path: Path) -> None:
    paths = _inputs(tmp_path)
    go = paths[1]
    with go.open("a") as stream:
        stream.write("T1\t21\tA21\t112\t112\t100\t0\t0\t1\n")
    with pytest.raises(ValueError, match="Duplicate"):
        _merge(tmp_path, paths)


def test_cross_species_refused(tmp_path: Path) -> None:
    paths = _inputs(tmp_path)
    go = paths[1]
    data = go.read_text().replace("112\t100\t0", "112\t999\t0", 1)
    go.write_text(data)
    with pytest.raises(ValueError, match="Cross-species"):
        _merge(tmp_path, paths)


def test_full_identity_conflict_refused(tmp_path: Path) -> None:
    paths = _inputs(tmp_path)
    full = paths[2]
    data = full.read_text().replace("A25\t113", "WRONG\t113")
    full.write_text(data)
    with pytest.raises(ValueError, match="Conflicting"):
        _merge(tmp_path, paths)


def test_existing_destination_refused(tmp_path: Path) -> None:
    paths = _inputs(tmp_path)
    (tmp_path / "merged.tsv").write_text("old")
    with pytest.raises(FileExistsError):
        _merge(tmp_path, paths)


def test_full_nonexact_refused(tmp_path: Path) -> None:
    paths = _inputs(tmp_path)
    full = paths[2]
    data = full.read_text().replace("\t1\tFULL_V2_EXACT", "\t0\tFULL_V2_EXACT", 1)
    full.write_text(data)
    with pytest.raises(ValueError, match="Invalid FULL exact evidence"):
        _merge(tmp_path, paths)


@pytest.mark.parametrize("species", ["562", "3702", "9606", "4896"])
def test_no_kingdom_specific_taxonomy_filter(tmp_path: Path, species: str) -> None:
    paths = _inputs(tmp_path)
    for path in paths:
        content = path.read_text()
        content = content.replace("\t100\t", f"\t{species}\t")
        content = content.replace("\t100\n", f"\t{species}\n")
        path.write_text(content)
    summary = _merge(tmp_path, paths)
    assert summary["cross_strain_exact"] == 2
    assert summary["cross_strain_nonexact"] == 1
