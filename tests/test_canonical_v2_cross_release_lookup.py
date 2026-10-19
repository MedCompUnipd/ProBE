"""Both EXACT releases supply lookup anchors, never cross-release GO."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from probe.canonical_v2.documentary_lookup import (
    OUTPUT,
    SUMMARY,
    discover_documentary_nonexact,
)
from probe.canonical_v2.exact_documentary_registry import (
    build_exact_documentary_register,
)
from probe.stage2c_candidate_union import OUTPUT_COLUMNS
from probe.workflow.artifacts import Artifact, StageManifest


def _tsv(path: Path, columns: tuple[str, ...], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=columns, delimiter="\t", lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def _rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def _fixture(root: Path) -> dict:
    targets = root / "targets.tsv"
    taxonomy = root / "taxonomy.tsv"
    _tsv(targets, ("target_id", "normalized_sequence"), [
        {"target_id": "T", "normalized_sequence": "ACDE"},
        {"target_id": "NO", "normalized_sequence": "MMMM"},
    ])
    _tsv(taxonomy, ("target_id", "resolved_taxid"), [
        {"target_id": "T", "resolved_taxid": "111"},
        {"target_id": "NO", "resolved_taxid": "111"},
    ])
    discovery = root / "discovery.tsv"
    exact = []
    for rid, taxid, full in ((1, "111", "0"), (2, "112", "1")):
        row = dict.fromkeys(OUTPUT_COLUMNS, "")
        row.update({
            "release": "R1", "target_id": "T", "rid": str(rid),
            "candidate_accession": f"A{rid}", "candidate_raw_ox": taxid,
            "target_resolved_taxid": "111",
            "candidate_resolved_taxid": taxid, "species_taxid": "100",
            "go_positive_species_wide": "1", "go_positive_exact": "1",
            "full_exact_species_wide": full,
            "strict_gate": "DENIED" if rid == 2 else "ELIGIBLE",
            "same_species_exact_gate": "ELIGIBLE",
        })
        exact.append(row)
    _tsv(discovery, OUTPUT_COLUMNS, exact)
    fasta_dir = root / "full"
    fasta_dir.mkdir()
    fasta = fasta_dir / "01A_full_v2.fasta"
    fasta.write_bytes(
        b">sp|A1|E1 RID=1 OX=111 FRAG=0 LEN=4 PE=1 SV=1 "
        b"GENE=N:ABC-1|S:ABC_1|L:LOC-1\nACDE\n"
        b">sp|A2|E2 RID=2 OX=112 FRAG=0 LEN=4 PE=1 SV=1 "
        b"GENE=N:ABD|L:LOC-2\nACDE\n"
        b">sp|A3|E3 RID=3 OX=111 FRAG=0 LEN=4 PE=1 SV=1 "
        b"GENE=N:ABC-1|L:LOC-1\nACDF\n"
        b">sp|A4|E4 RID=4 OX=111 FRAG=0 LEN=4 PE=1 SV=1 "
        b"GENE=N:ABC1|L:LOC2\nACDG\n"
        b">sp|A5|E5 RID=5 OX=111 FRAG=0 LEN=4 PE=1 SV=1 "
        b"GENE=N:ABD|L:LOC-2\nACDH\n"
        b">sp|A6|E6 RID=6 OX=111 FRAG=0 LEN=4 PE=1 SV=1 "
        b"GENE=N:OTHER|L:FREE-1\nMMMN\n"
        b">sp|A7|E7 RID=7 OX=112 FRAG=0 LEN=4 PE=1 SV=1 "
        b"GENE=N:ABC-1|L:LOC-1\nACDI\n"
    )
    digest = hashlib.sha256(fasta.read_bytes()).hexdigest()
    StageManifest(
        stage="01A_FULL", role="START", release_id="R1",
        parameters={}, ontology=None,
        implementation={"name": "fixture", "version": "1"}, inputs=(),
        outputs=(Artifact("01A_full_v2.fasta", fasta.stat().st_size, digest),),
    ).write(fasta_dir / "manifest.json")
    register = root / "exact"
    build_exact_documentary_register(
        release="R1", discovery=discovery, targets=targets,
        full_manifest=fasta_dir / "manifest.json", output_dir=register,
    )
    index = root / "independent.tsv"
    columns = (
        "release", "target_id", "key_type", "key_value",
        "source_roles", "primary_bucket", "key_target_multiplicity",
    )
    _tsv(index, columns, [{
        "release": "R1", "target_id": "NO", "key_type": "ORDERED_LOCUS",
        "key_value": "FREE-1", "source_roles": "TARGET_DIRECT",
        "primary_bucket": "T:111", "key_target_multiplicity": "1",
    }])
    ox_tax = root / "ox.tsv"
    _tsv(ox_tax, ("raw_ox", "resolved_taxid", "species_taxid"), [
        {"raw_ox": "111", "resolved_taxid": "111", "species_taxid": "100"},
        {"raw_ox": "112", "resolved_taxid": "112", "species_taxid": "100"},
    ])
    return {
        "release": "R1", "targets": targets, "target_taxonomy": taxonomy,
        "exact_register": register, "independent_index": index,
        "ox_taxonomy": ox_tax, "full_manifest": fasta_dir / "manifest.json",
        "destination": root / "matches",
    }


def _end_register(root: Path, targets: Path) -> tuple[Path, Path]:
    """One END EXACT has a key absent from START EXACT observations."""
    root.mkdir()
    discovery = root / "discovery.tsv"
    row = dict.fromkeys(OUTPUT_COLUMNS, "")
    row.update({
        "release": "R2",
        "target_id": "T",
        "rid": "1",
        "candidate_accession": "END1",
        "candidate_raw_ox": "111",
        "target_resolved_taxid": "111",
        "candidate_resolved_taxid": "111",
        "species_taxid": "100",
        "go_positive_exact": "1",
        "full_exact_species_wide": "1",
        "strict_gate": "ELIGIBLE",
        "same_species_exact_gate": "ELIGIBLE",
    })
    _tsv(discovery, OUTPUT_COLUMNS, [row])
    full = root / "full"
    full.mkdir()
    fasta = full / "01A_full_v2.fasta"
    fasta.write_bytes(
        b">sp|END1|END1 RID=1 OX=111 FRAG=0 LEN=4 PE=1 SV=1 "
        b"GENE=N:OTHER|L:FREE-1\nACDE\n"
        b">sp|END2|END2 RID=2 OX=111 FRAG=0 LEN=4 PE=1 SV=1 "
        b"GENE=N:ABC-1|L:LOC-1\nACDF\n"
    )
    digest = hashlib.sha256(fasta.read_bytes()).hexdigest()
    StageManifest(
        stage="01A_FULL", role="END", release_id="R2",
        parameters={}, ontology=None,
        implementation={"name": "fixture", "version": "1"},
        inputs=(),
        outputs=(Artifact("01A_full_v2.fasta", fasta.stat().st_size, digest),),
    ).write(full / "manifest.json")
    register = root / "exact"
    build_exact_documentary_register(
        release="R2", discovery=discovery, targets=targets,
        full_manifest=full / "manifest.json", output_dir=register,
    )
    index = root / "independent.tsv"
    _tsv(index, (
        "release", "target_id", "key_type", "key_value",
        "source_roles", "primary_bucket", "key_target_multiplicity",
    ), [])
    return register, index


def test_end_only_exact_key_finds_start_nonexact(tmp_path: Path) -> None:
    start = _fixture(tmp_path)
    end_register, _ = _end_register(tmp_path / "end", start["targets"])
    start["other_exact_register"] = end_register
    start["other_release"] = "R2"
    summary = discover_documentary_nonexact(**start)
    rows = _rows(start["destination"] / OUTPUT)
    end_sourced = [row for row in rows if (
        row["target_id"] == "T"
        and row["candidate_rid"] == "6"
        and row["exact_origin_release"] == "R2"
    )]
    assert end_sourced
    assert all(row["release"] == "R1" for row in rows)
    assert all(row["candidate_resolved_taxid"] == "111" for row in rows)
    assert all(row["exact_source_rid"] == "1" for row in end_sourced)
    assert summary["documentary_query_mode"] == "RETROSPECTIVE_START_END_UNION"
    assert summary["nonexact_dp_authorized"] == 0
    assert summary["go_donor_decisions"] == 0
    assert summary["other_exact_release"] == "R2"
    assert json.loads((start["destination"] / SUMMARY).read_text()) == summary


def test_start_only_exact_key_finds_end_nonexact(tmp_path: Path) -> None:
    start = _fixture(tmp_path)
    end_register, end_index = _end_register(
        tmp_path / "end", start["targets"],
    )
    result = discover_documentary_nonexact(
        release="R2",
        targets=start["targets"],
        target_taxonomy=start["target_taxonomy"],
        exact_register=end_register,
        other_exact_register=start["exact_register"],
        other_release="R1",
        independent_index=end_index,
        ox_taxonomy=start["ox_taxonomy"],
        full_manifest=tmp_path / "end/full/manifest.json",
        destination=tmp_path / "end/matches",
    )
    matches = _rows(tmp_path / "end/matches" / OUTPUT)
    assert any(
        row["release"] == "R2"
        and row["target_id"] == "T"
        and row["candidate_rid"] == "2"
        and row["exact_origin_release"] == "R1"
        and row["source_key_value"] == "LOC-1"
        for row in matches
    )
    assert result["count"]["other_release_exact_source_rows"] > 0


def test_distinct_release_rids_do_not_collapse(tmp_path: Path) -> None:
    start = _fixture(tmp_path)
    end_register, _ = _end_register(tmp_path / "end", start["targets"])
    start["other_exact_register"] = end_register
    start["other_release"] = "R2"
    discover_documentary_nonexact(**start)
    matches = _rows(start["destination"] / OUTPUT)
    assert any(row["exact_origin_release"] == "R1" for row in matches)
    assert any(row["exact_origin_release"] == "R2" for row in matches)
    assert any(
        row["candidate_rid"] == "6" and row["exact_origin_release"] == "R2"
        for row in matches
    )


def test_other_register_sha_tamper_rejected(tmp_path: Path) -> None:
    start = _fixture(tmp_path)
    end_register, _ = _end_register(tmp_path / "end", start["targets"])
    start["other_exact_register"] = end_register
    start["other_release"] = "R2"
    path = end_register / "exact_documentary_keys.tsv"
    path.write_bytes(path.read_bytes() + b"wrong\n")
    with pytest.raises(ValueError, match="invalid SHA"):
        discover_documentary_nonexact(**start)
    assert not start["destination"].exists()


def test_invalid_cross_release_configuration_rejected(tmp_path: Path) -> None:
    start = _fixture(tmp_path)
    end_register, _ = _end_register(tmp_path / "end", start["targets"])
    start["other_exact_register"] = end_register
    with pytest.raises(ValueError, match="must be paired"):
        discover_documentary_nonexact(**start)
    start["other_release"] = "R1"
    with pytest.raises(ValueError, match="distinct release"):
        discover_documentary_nonexact(**start)


def test_other_register_target_mismatch_rejected(tmp_path: Path) -> None:
    start = _fixture(tmp_path)
    different_targets = tmp_path / "other_targets.tsv"
    _tsv(different_targets, ("target_id", "normalized_sequence"), [
        {"target_id": "T", "normalized_sequence": "ACDE"},
        {"target_id": "NO", "normalized_sequence": "MMMN"},
    ])
    end_register, _ = _end_register(tmp_path / "end", different_targets)
    start["other_exact_register"] = end_register
    start["other_release"] = "R2"
    with pytest.raises(ValueError, match="invalid SHA"):
        discover_documentary_nonexact(**start)


def test_only_same_taxid_candidates_scanned(tmp_path: Path) -> None:
    start = _fixture(tmp_path)
    end_register, _ = _end_register(tmp_path / "end", start["targets"])
    start["other_exact_register"] = end_register
    start["other_release"] = "R2"
    discover_documentary_nonexact(**start)
    rows = _rows(start["destination"] / OUTPUT)
    assert not any(row["candidate_rid"] == "7" for row in rows)
    assert all(
        row["candidate_resolved_taxid"] == row["target_resolved_taxid"]
        for row in rows
    )
