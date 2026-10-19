"""Literal IUPAC EXACT identity retains taxonomy and fragment safeguards."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from test_canonical_v2_exact_documentary_registry import (
    _build,
    _fixture,
    _rows,
    _write_tsv,
)
from test_stage2c_full_exact_scan import run_scan
from test_stage2c_resolver_variant import SEQ, decision, fixture
from test_stage2c_strain_gate import BASE

from probe.canonical_v2.exact_documentary_registry import OUTPUT
from probe.cross_strain import (
    assess_strain_gate,
    literal_protein,
    unambiguous_protein,
)


@pytest.mark.parametrize("symbol", "BJXZ")
def test_literal_alphabet_does_not_redefine_unambiguous(symbol: str) -> None:
    sequence = "AC" + symbol + "DEUO"
    assert literal_protein(sequence)
    assert not unambiguous_protein(sequence)
    assert unambiguous_protein("ACDEUO")


@pytest.mark.parametrize("sequence", ["", "AC*D", "AC-D", "AcDE", "AC D", "AC?D"])
def test_invalid_literal_alphabet_is_not_exact(sequence: str) -> None:
    assert not literal_protein(sequence)


@pytest.mark.parametrize(
    "target,candidate",
    [
        ("ABDE", "ADDE"),
        ("AJDE", "AIDE"),
        ("AJDE", "ALDE"),
        ("AXDE", "AADE"),
        ("AZDE", "AEDE"),
        ("ABDE", "ADBE"),
        ("ABDE", "ABDF"),
        ("ABDE", "ABDEA"),
    ],
)
def test_same_ambiguous_position_is_not_sufficient(
    target: str,
    candidate: str,
) -> None:
    gate = assess_strain_gate(
        replace(BASE, target_sequence=target, candidate_sequence=candidate),
        policy="same_species_exact",
    )
    assert gate.status == "DENIED"
    assert gate.reason == "NONIDENTICAL_FULL_SEQUENCE"


@pytest.mark.parametrize("symbol", "BJXZ")
def test_ambiguous_exact_does_not_cross_species_or_missing_taxonomy(
    symbol: str,
) -> None:
    sequence = "AC" + symbol + "DE"
    row = replace(BASE, target_sequence=sequence, candidate_sequence=sequence)
    for evidence, reason in (
        (replace(row, candidate_species_taxid="9606"), "CROSS_SPECIES"),
        (replace(row, candidate_taxid=""), "UNRESOLVED_TAXONOMY"),
    ):
        gate = assess_strain_gate(evidence, policy="same_species_exact")
        assert gate.status == "DENIED"
        assert gate.reason == reason
    assert assess_strain_gate(row, policy="strict").status == "DENIED"


@pytest.mark.parametrize("symbol", "BJXZ")
@pytest.mark.parametrize(
    "fragment_policy,donor", [("allow", True), ("report_only", False)]
)
def test_literal_ambiguous_fragment_keeps_documentary_policy(
    symbol: str,
    fragment_policy: str,
    donor: bool,
) -> None:
    case = fixture(fragment=True, audit=True, documentary=True)
    key = next(iter(case[3]))
    sequence = SEQ[:4] + symbol + SEQ[5:]
    case[3][key] = (sequence, sequence)
    result = decision(case, documentary_fragment=fragment_policy)
    assert result.decision == "ACCEPTED"
    assert result.annotation_donor is donor


@pytest.mark.parametrize("symbol", "BJXZ")
def test_literal_ambiguous_fragment_still_needs_independent_document(
    symbol: str,
) -> None:
    case = fixture(fragment=True, documentary=False)
    key = next(iter(case[3]))
    sequence = SEQ[:4] + symbol + SEQ[5:]
    case[3][key] = (sequence, sequence)
    result = decision(case)
    assert not result.annotation_donor
    assert "FRAGMENT_NEEDS_DOCUMENTARY" in result.warnings


@pytest.mark.parametrize("symbol", "BJXZ")
def test_exact_register_verifies_both_taxonomic_scopes(
    tmp_path: Path,
    symbol: str,
) -> None:
    paths = _fixture(tmp_path)
    sequence = f"A{symbol}DE"
    targets = _rows(paths["targets"])
    targets[0]["normalized_sequence"] = sequence
    _write_tsv(paths["targets"], ("target_id", "normalized_sequence"), targets)
    manifest = paths["full_manifest"]
    fasta = manifest.parent / "01A_full_v2.fasta"
    fasta.write_bytes(
        fasta.read_bytes().replace(b"ACDE\n", sequence.encode("ascii") + b"\n")
    )
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["outputs"][0]["sha256"] = hashlib.sha256(fasta.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    summary = _build(paths)
    keys = _rows(paths["output_dir"] / OUTPUT)
    assert {row["tax_scope"] for row in keys} == {"SAME_TAXID", "SAME_SPECIES"}
    assert {row["source_full_sequence_sha256"] for row in keys} == {
        hashlib.sha256(sequence.encode("ascii")).hexdigest()
    }
    assert summary["counts"]["literal_verified_exact"] == 2
    assert summary["counts"]["literal_verified_ambiguous_exact"] == 2
    assert summary["go_donor_decisions"] == 0
    assert summary["dp_jobs_authorized"] == 0


@pytest.fixture(scope="module")
def literal_scanner(tmp_path_factory: pytest.TempPathFactory) -> Path:
    compiler = (
        shutil.which("g++")
        or shutil.which("c++")
        or shutil.which("x86_64-conda-linux-gnu-c++")
    )
    if compiler is None:
        pytest.skip("C++20 compiler unavailable")
    root = Path(__file__).resolve().parents[1]
    source = root / "cpp/src/probe_full_exact_cross_strain_v1.cpp"
    executable = tmp_path_factory.mktemp("literal_exact_cpp") / "exact"
    subprocess.run(
        [compiler, "-std=c++20", "-O1", str(source), "-o", str(executable)],
        check=True,
        capture_output=True,
        text=True,
    )
    return executable


@pytest.mark.parametrize("symbol", "BJXZ")
def test_cpp_full_scan_literal_identity_includes_ambiguity(
    literal_scanner: Path,
    tmp_path: Path,
    symbol: str,
) -> None:
    sequence = "AC" + symbol + "DE"
    pairs, summary = run_scan(
        literal_scanner,
        tmp_path,
        [
            ("112", "0", sequence),
            ("112", "0", "ACADE"),
            ("112", "0", "ACDE" + symbol),
            ("9606", "0", sequence),
            ("112", "1", sequence),
            ("111", "0", sequence),
        ],
        target_sequence=sequence,
    )
    assert [row["rid"] for row in pairs] == ["1", "6"]
    assert pairs[0]["exact_full_sequence"] == "1"
    assert pairs[0]["candidate_resolved_taxid"] == "112"
    assert pairs[1]["candidate_resolved_taxid"] == "111"
    assert summary["full_literal_exact_pairs"] == "2"
    assert summary["same_taxid_exact_pairs"] == "1"
    assert summary["cross_strain_exact_pairs"] == "1"
    assert summary["sequence_identity_policy"] == "literal-iupac26-v1"
    assert summary["ambiguous_symbols_are_wildcards"] == "0"
    assert summary["fragments_skipped"] == "1"
    assert summary["invalid_alphabet_sequences_skipped"] == "0"
    assert summary["ambiguous_sequences_observed"] == "4"


@pytest.mark.parametrize("invalid", ["*", "?", "-", "a"])
def test_cpp_rejects_non_iupac_identity(
    literal_scanner: Path,
    tmp_path: Path,
    invalid: str,
) -> None:
    sequence = "AC" + invalid + "DE"
    pairs, summary = run_scan(
        literal_scanner,
        tmp_path,
        [("112", "0", sequence)],
        target_sequence=sequence,
    )
    assert pairs == []
    assert summary["targets_indexed_sequences"] == "0"
    assert summary["invalid_alphabet_sequences_skipped"] == "1"
