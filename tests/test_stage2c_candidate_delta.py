"""Synthetic science contracts for a Stage 2C/05A2 candidate reconciliation."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from probe.stage2c_candidate_delta import reconcile_candidate_delta
from probe.stage2c_candidate_union import OUTPUT_COLUMNS


def write_tsv(path: Path, fields, rows) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def record(target, rid, *, tax="12", exact="1", full="0") -> dict:
    row = dict.fromkeys(OUTPUT_COLUMNS, "")
    row.update(
        release="2023_01",
        target_id=target,
        rid=rid,
        candidate_accession=f"P{rid}",
        candidate_raw_ox=tax,
        target_resolved_taxid="11",
        candidate_resolved_taxid=tax,
        species_taxid="10",
        go_positive_species_wide="1",
        full_exact_species_wide=full,
        go_positive_exact=exact,
        go_positive_record_number="4",
        full_record_number="8" if full == "1" else "",
        strict_gate="DENIED" if tax != "11" else "DEFER_TO_DOCUMENTARY",
        same_species_exact_gate=(
            "ELIGIBLE"
            if tax != "11" and exact == "1"
            else "DENIED"
            if tax != "11"
            else "DEFER_TO_DOCUMENTARY"
        ),
    )
    return row


def fixtures(tmp_path):
    legacy = tmp_path / "legacy.tsv"
    discovery = tmp_path / "discovery.tsv"
    out = tmp_path / "delta.tsv"
    summary = tmp_path / "delta.json"
    write_tsv(
        legacy,
        ("release", "target_id", "rid", "candidate_primary_accession"),
        [
            {
                "release": "2023_01",
                "target_id": "T1",
                "rid": "3",
                "candidate_primary_accession": "P3",
            }
        ],
    )
    write_tsv(
        discovery,
        OUTPUT_COLUMNS,
        [
            record("T1", "3"),
            record("T1", "4"),
            record("T2", "5", exact="0"),
            record("T3", "6", tax="11"),
        ],
    )
    return legacy, discovery, out, summary


def run(files):
    legacy, discovery, output, summary = files
    return reconcile_candidate_delta(
        release="2023_01",
        legacy_union=legacy,
        discovery_union=discovery,
        output=output,
        summary=summary,
    )


def test_delta_never_makes_go_donor(tmp_path):
    files = fixtures(tmp_path)
    result = run(files)
    assert result["counts"]["legacy_candidates"] == 1
    assert result["counts"]["already_in_05a2"] == 1
    assert result["counts"]["new_candidates"] == 3
    assert result["counts"]["new_cross_strain_exact"] == 1
    assert result["counts"]["new_cross_strain_nonexact"] == 1
    assert result["counts"]["new_same_taxid"] == 1
    assert result["scientific_decision"] == "NO_GO_DONOR_DECISION"
    with files[2].open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    assert {row["rid"] for row in rows} == {"4", "5", "6"}
    assert all(
        row["integration_state"] == "NEEDS_EVIDENCE_RECONSTRUCTION" for row in rows
    )
    assert all("annotation_donor" not in row for row in rows)


def test_reject_overwrite(tmp_path):
    files = fixtures(tmp_path)
    run(files)
    with pytest.raises(FileExistsError):
        run(files)


def test_reject_gate_mismatch(tmp_path):
    files = fixtures(tmp_path)
    discovery = files[1]
    rows = list(csv.DictReader(discovery.open(encoding="utf-8"), delimiter="\t"))
    rows[1]["same_species_exact_gate"] = "DENIED"
    write_tsv(discovery, OUTPUT_COLUMNS, rows)
    with pytest.raises(ValueError, match="gates contradict"):
        run(files)
    assert not files[2].exists()


def test_reject_duplicate_source(tmp_path):
    files = fixtures(tmp_path)
    discovery = files[1]
    rows = list(csv.DictReader(discovery.open(encoding="utf-8"), delimiter="\t"))
    write_tsv(discovery, OUTPUT_COLUMNS, [*rows, rows[-1]])
    with pytest.raises(ValueError, match="Duplicate discovery"):
        run(files)


def test_reject_unknown_release(tmp_path):
    files = fixtures(tmp_path)
    discovery = files[1]
    rows = list(csv.DictReader(discovery.open(encoding="utf-8"), delimiter="\t"))
    rows[0]["release"] = "2026_03"
    write_tsv(discovery, OUTPUT_COLUMNS, rows)
    with pytest.raises(ValueError, match="release mismatch"):
        run(files)


def test_reject_accession_collision(tmp_path):
    files = fixtures(tmp_path)
    discovery = files[1]
    rows = list(csv.DictReader(discovery.open(encoding="utf-8"), delimiter="\t"))
    rows[0]["candidate_accession"] = "UNEXPECTED"
    write_tsv(discovery, OUTPUT_COLUMNS, rows)
    with pytest.raises(ValueError, match="Conflicting accession"):
        run(files)


def test_reject_same_taxid_full_match(tmp_path):
    files = fixtures(tmp_path)
    discovery = files[1]
    rows = list(csv.DictReader(discovery.open(encoding="utf-8"), delimiter="\t"))
    rows[-1]["full_exact_species_wide"] = "1"
    write_tsv(discovery, OUTPUT_COLUMNS, rows)
    with pytest.raises(ValueError, match="FULL exact provenance"):
        run(files)


def test_legacy_duplicate_rejected(tmp_path):
    files = fixtures(tmp_path)
    legacy = files[0]
    rows = list(csv.DictReader(legacy.open(encoding="utf-8"), delimiter="\t"))
    write_tsv(
        legacy,
        ("release", "target_id", "rid", "candidate_primary_accession"),
        [*rows, rows[0]],
    )
    with pytest.raises(ValueError, match="Duplicate legacy"):
        run(files)
