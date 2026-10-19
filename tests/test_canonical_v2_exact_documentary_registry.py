"""EXACT documentary observations never become GO/DP approvals."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from probe.canonical_v2.exact_documentary_registry import (
    OUTPUT,
    SUMMARY,
    build_exact_documentary_register,
)
from probe.stage2c_candidate_union import OUTPUT_COLUMNS
from probe.workflow.artifacts import Artifact, StageManifest


def _write_tsv(path: Path, fields: tuple[str, ...], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=fields,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def _fixture(tmp_path: Path) -> dict[str, Path]:
    target = tmp_path / "01_targets.tsv"
    _write_tsv(
        target,
        ("target_id", "normalized_sequence"),
        [
            {"target_id": "T", "normalized_sequence": "ACDE"},
            {"target_id": "T0", "normalized_sequence": "AAAA"},
        ],
    )
    union = tmp_path / "03_unified_discovery.tsv"
    rows = []
    for rid, name, candidate_tax, exact, full in (
        (1, "A1", "111", "1", "0"),
        (2, "A2", "112", "0", "1"),
        (3, "A3", "111", "0", "0"),
    ):
        row = dict.fromkeys(OUTPUT_COLUMNS, "")
        row.update(
            {
                "release": "R1",
                "target_id": "T",
                "rid": str(rid),
                "candidate_accession": name,
                "candidate_raw_ox": candidate_tax,
                "target_resolved_taxid": "111",
                "candidate_resolved_taxid": candidate_tax,
                "species_taxid": "100",
                "go_positive_exact": exact,
                "full_exact_species_wide": full,
                "strict_gate": "DENIED" if rid == 2 else "DEFER_TO_DOCUMENTARY",
                "same_species_exact_gate": (
                    "ELIGIBLE" if rid == 2 else "DEFER_TO_DOCUMENTARY"
                ),
                "go_positive_species_wide": "1",
                "go_positive_record_number": "1",
            }
        )
        rows.append(row)
    _write_tsv(union, OUTPUT_COLUMNS, rows)
    full = tmp_path / "01A_FULL"
    full.mkdir()
    fasta = full / "01A_full_v2.fasta"
    with fasta.open("wb") as stream:
        for rid, seq, ox in ((1, "ACDE", 111), (2, "ACDE", 112), (3, "AAAA", 111)):
            stream.write(
                (
                    f">sp|A{rid}|ENTRY{rid} RID={rid} OX={ox} FRAG=0 "
                    "LEN=4 PE=1 SV=1 "
                    "SEC=ALIAS-1,ALIAS_1 "
                    "GENE=N:ABC-1|S:ABC1|L:LOC-01|O:SO:0001217\n"
                    f"{seq}\n"
                ).encode("ascii")
            )
    digest = hashlib.sha256(fasta.read_bytes()).hexdigest()
    StageManifest(
        stage="01A_FULL",
        role="START",
        release_id="R1",
        parameters={},
        ontology=None,
        implementation={"name": "fixture", "version": "1"},
        inputs=(),
        outputs=(Artifact("01A_full_v2.fasta", fasta.stat().st_size, digest),),
    ).write(full / "manifest.json")
    return {
        "discovery": union,
        "targets": target,
        "full_manifest": full / "manifest.json",
        "output_dir": tmp_path / "keys",
    }


def _build(paths: dict[str, Path]) -> dict:
    return build_exact_documentary_register(release="R1", **paths)


def _rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def test_exact_lane_keys_not_approvals(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    result = _build(paths)
    rows = _rows(paths["output_dir"] / OUTPUT)
    assert {r["exact_candidate_rid"] for r in rows} == {"1", "2"}
    assert result["counts"]["nonexact_discovery_rows"] == 1
    assert result["counts"]["literal_verified_exact"] == 2
    assert all(r["eligible_as_existing_04c_key"] == "0" for r in rows)
    assert result["go_donor_decisions"] == 0
    assert result["dp_jobs_authorized"] == 0
    assert result["lexical_hints_authorize_matches"] is False
    assert result["exact_keys_authorize_04c_lineage"] is False


def test_cross_subtaxon_keys_cannot_seed_nonexact_dp(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    _build(paths)
    rows = _rows(paths["output_dir"] / OUTPUT)
    assert {
        r["same_taxid_context"] for r in rows if r["exact_candidate_rid"] == "2"
    } == {"0"}
    assert {
        r["same_taxid_context"] for r in rows if r["exact_candidate_rid"] == "1"
    } == {"1"}


def test_similar_names_not_automatic_alias(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    _build(paths)
    rows = _rows(paths["output_dir"] / OUTPUT)
    names = [
        r
        for r in rows
        if r["key_type"]
        in {
            "GENE_NAME",
            "GENE_SYNONYM",
        }
    ]
    assert {r["key_value"] for r in names} == {"ABC-1", "ABC1"}
    assert len({r["lexical_hint"] for r in names}) == 1
    assert all(r["eligible_as_existing_04c_key"] == "0" for r in names)


def test_ontology_orf_not_validated_locus(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    _build(paths)
    rows = _rows(paths["output_dir"] / OUTPUT)
    assert {r["key_status"] for r in rows if r["key_type"] == "ORF"} == {
        "NONIDENTIFYING_TYPED_KEY"
    }


def test_no_exact_no_key_but_nonexact_still_discovered(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    union = paths["discovery"]
    rows = _rows(union)
    for row in rows:
        row["go_positive_exact"] = "0"
        row["full_exact_species_wide"] = "0"
    _write_tsv(union, OUTPUT_COLUMNS, rows)
    result = _build(paths)
    assert result["counts"]["nonexact_discovery_rows"] == 3
    assert _rows(paths["output_dir"] / OUTPUT) == []


def test_full_sequence_disagreement_is_fatal(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    target = paths["targets"]
    rows = _rows(target)
    rows[0]["normalized_sequence"] = "ACDF"
    _write_tsv(target, ("target_id", "normalized_sequence"), rows)
    with pytest.raises(ValueError, match="EXACT conflicts"):
        _build(paths)
    assert not paths["output_dir"].exists()


def test_tampered_full_fasta_rejected(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    fasta = paths["full_manifest"].parent / "01A_full_v2.fasta"
    fasta.write_bytes(fasta.read_bytes().replace(b"ACDE\n", b"ACDF\n"))
    with pytest.raises(ValueError):
        _build(paths)
    assert not paths["output_dir"].exists()


def test_wrong_release_and_duplicate_discovery_rejected(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    with pytest.raises(ValueError, match="Wrong FULL manifest"):
        build_exact_documentary_register(release="R2", **paths)
    rows = _rows(paths["discovery"])
    rows.append(dict(rows[0]))
    _write_tsv(paths["discovery"], OUTPUT_COLUMNS, rows)
    with pytest.raises(ValueError, match="Duplicate discovery"):
        _build(paths)


def test_wrong_cross_subtaxon_gate_rejected(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    rows = _rows(paths["discovery"])
    rows[1]["same_species_exact_gate"] = "DENIED"
    _write_tsv(paths["discovery"], OUTPUT_COLUMNS, rows)
    with pytest.raises(ValueError, match="species gate"):
        _build(paths)


def test_immutable_output_and_manifest_checksums(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    summary = _build(paths)
    assert json.loads((paths["output_dir"] / SUMMARY).read_text()) == summary
    assert (
        summary["output_sha256"][OUTPUT]
        == hashlib.sha256((paths["output_dir"] / OUTPUT).read_bytes()).hexdigest()
    )
    with pytest.raises(FileExistsError, match="occupied"):
        _build(paths)


@pytest.mark.parametrize("symbol", "BJXZ")
def test_ambiguous_literal_match_provides_keys_not_approval(
    tmp_path: Path,
    symbol: str,
) -> None:
    paths = _fixture(tmp_path)
    target = paths["targets"]
    rows = _rows(target)
    sequence = f"A{symbol}DE"
    rows[0]["normalized_sequence"] = sequence
    _write_tsv(target, ("target_id", "normalized_sequence"), rows)
    union = paths["discovery"]
    discoveries = _rows(union)
    discoveries[1]["full_exact_species_wide"] = "0"
    _write_tsv(union, OUTPUT_COLUMNS, discoveries)
    manifest = paths["full_manifest"]
    fasta = manifest.parent / "01A_full_v2.fasta"
    fasta.write_bytes(
        fasta.read_bytes().replace(b"ACDE\n", sequence.encode("ascii") + b"\n", 1)
    )
    raw = json.loads(manifest.read_text())
    raw["outputs"][0]["sha256"] = hashlib.sha256(fasta.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(raw) + "\n")
    summary = _build(paths)
    assert summary["counts"]["literal_verified_ambiguous_exact"] == 1
    assert summary["counts"]["literal_verified_exact"] == 1
    assert summary["sequence_identity_policy"] == "literal-iupac26-v1"
    assert summary["ambiguous_symbols_are_wildcards"] is False
    observed = _rows(paths["output_dir"] / OUTPUT)
    assert {row["exact_candidate_rid"] for row in observed} == {"1"}
    assert all(row["eligible_as_existing_04c_key"] == "0" for row in observed)
    assert summary["go_donor_decisions"] == 0
    assert summary["dp_jobs_authorized"] == 0


def test_fragment_literal_exact_is_validated_and_generates_keys(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    manifest = paths["full_manifest"]
    fasta = manifest.parent / "01A_full_v2.fasta"
    fasta.write_bytes(fasta.read_bytes().replace(b"FRAG=0", b"FRAG=1", 1))
    raw = json.loads(manifest.read_text())
    raw["outputs"][0]["sha256"] = hashlib.sha256(fasta.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(raw) + "\n")
    summary = _build(paths)
    assert summary["counts"]["literal_verified_fragment_exact"] == 1
    assert summary["counts"]["literal_verified_exact"] == 2
    assert {r["exact_candidate_rid"] for r in _rows(paths["output_dir"] / OUTPUT)} == {
        "1",
        "2",
    }
