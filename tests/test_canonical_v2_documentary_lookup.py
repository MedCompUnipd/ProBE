"""Synthetic registry-to-documentary lookup integration tests."""

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


def test_two_lane_policy_and_nonexact_literal(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    result = discover_documentary_nonexact(**paths)
    rows = _rows(paths["destination"] / OUTPUT)
    assert result["nonexact_dp_authorized"] == 0
    assert result["go_donor_decisions"] == 0
    assert any(r["target_id"] == "NO" and r["candidate_rid"] == "6"
               and r["source_kind"] == "INDEPENDENT_04D1" for r in rows)
    assert any(r["target_id"] == "T" and r["candidate_rid"] == "3"
               and r["source_kind"] == "EXACT" and r["match_class"] == "LITERAL"
               and r["source_policy_strict"] == "1" for r in rows)
    assert any(r["target_id"] == "T" and r["candidate_rid"] == "5"
               and r["source_policy_strict"] == "0"
               and r["source_policy_same_species_exact"] == "1" for r in rows)
    assert not any(r["candidate_rid"] == "7" for r in rows)
    assert json.loads((paths["destination"] / SUMMARY).read_text()) == result


def test_normalization_is_hint_not_literal(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    discover_documentary_nonexact(**paths)
    rows = _rows(paths["destination"] / OUTPUT)
    assert any(r["candidate_rid"] == "4"
               and r["match_class"] == "NORMALIZED_HINT" for r in rows)
    assert not any(r["candidate_rid"] == "4"
                   and r["match_class"] == "LITERAL" for r in rows)


def test_tamper_exact_register_rejected(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    exact = paths["exact_register"] / "exact_documentary_keys.tsv"
    exact.write_bytes(exact.read_bytes() + b"corruption\n")
    with pytest.raises(ValueError, match="invalid SHA"):
        discover_documentary_nonexact(**paths)
    assert not paths["destination"].exists()


def test_tamper_full_sha_rejected(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    fasta = paths["full_manifest"].parent / "01A_full_v2.fasta"
    fasta.write_bytes(fasta.read_bytes().replace(b"ACDF\n", b"ACDM\n"))
    with pytest.raises(ValueError, match="SHA256"):
        discover_documentary_nonexact(**paths)
    assert not paths["destination"].exists()


def test_immutable_destination(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    discover_documentary_nonexact(**paths)
    with pytest.raises(FileExistsError, match="already exists"):
        discover_documentary_nonexact(**paths)


def test_explicit_same_taxid_gate(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    rows = _rows(paths["ox_taxonomy"])
    rows[0]["resolved_taxid"] = "999"
    _tsv(paths["ox_taxonomy"], ("raw_ox", "resolved_taxid", "species_taxid"), rows)
    discover_documentary_nonexact(**paths)
    matches = _rows(paths["destination"] / OUTPUT)
    assert not any(r["candidate_rid"] == "3" for r in matches)


def test_duplicate_taxonomy_rejected(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    rows = _rows(paths["target_taxonomy"])
    rows.append(rows[0])
    _tsv(paths["target_taxonomy"],
         ("target_id", "resolved_taxid"), rows)
    with pytest.raises(ValueError, match="duplicated target taxonomy"):
        discover_documentary_nonexact(**paths)
