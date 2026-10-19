"""Contract tests for the independent Stage 2C strain eligibility gate."""

from dataclasses import replace

import pytest

from probe.cross_strain import StrainGateInput, assess_strain_gate

SEQ = "ACDEFGHIKLMNPQRSTVWY"
BASE = StrainGateInput(
    target_taxid="111",
    candidate_taxid="112",
    target_species_taxid="562",
    candidate_species_taxid="562",
    target_sequence=SEQ,
    candidate_sequence=SEQ,
)


def test_cross_strain_identical_exact_policy_is_eligible() -> None:
    result = assess_strain_gate(BASE, policy="same_species_exact")
    assert result.status == "ELIGIBLE"
    assert result.exact_full_sequence


def test_cross_strain_strict_does_not_transfer() -> None:
    result = assess_strain_gate(BASE, policy="strict")
    assert result.status == "DENIED"
    assert result.reason == "STRICT_CROSS_STRAIN"


@pytest.mark.parametrize(
    "candidate",
    [SEQ[:-1] + "A", SEQ[:-1], SEQ + "A", ""],
)
def test_cross_strain_sequence_variants_are_denied(candidate: str) -> None:
    row = replace(BASE, candidate_sequence=candidate)
    result = assess_strain_gate(row, policy="same_species_exact")
    assert result.status == "DENIED"
    assert result.reason == "NONIDENTICAL_FULL_SEQUENCE"


def test_same_taxid_does_not_override_documentary_rules() -> None:
    row = replace(BASE, candidate_taxid="111", candidate_sequence="QQ")
    result = assess_strain_gate(row, policy="strict")
    assert result.status == "DEFER_TO_DOCUMENTARY"


def test_cross_species_exact_is_denied() -> None:
    row = replace(BASE, candidate_species_taxid="9606")
    result = assess_strain_gate(row, policy="same_species_exact")
    assert result.reason == "CROSS_SPECIES"


@pytest.mark.parametrize("taxid", ["", "0", "bad"])
def test_unresolved_taxonomy_is_denied(taxid: str) -> None:
    row = replace(BASE, candidate_taxid=taxid)
    result = assess_strain_gate(row, policy="same_species_exact")
    assert result.reason == "UNRESOLVED_TAXONOMY"


def test_fragment_requires_independent_documentary_support() -> None:
    row = replace(BASE, fragment_record=True)
    result = assess_strain_gate(row, policy="same_species_exact")
    assert result.reason == "FRAGMENT_NEEDS_DOCUMENTARY"
    assert result.exact_full_sequence
    supported = replace(row, verified_documentary_fragment=True)
    assert (
        assess_strain_gate(supported, policy="same_species_exact").status == "ELIGIBLE"
    )


@pytest.mark.parametrize("residue", ["B", "J", "X", "Z"])
def test_iupac26_literal_ambiguous_sequences_are_exact(residue: str) -> None:
    row = replace(
        BASE,
        target_sequence=SEQ + residue,
        candidate_sequence=SEQ + residue,
    )
    result = assess_strain_gate(row, policy="same_species_exact")
    assert result.status == "ELIGIBLE"
    assert result.reason == "EXACT_SAME_SPECIES"
    assert result.exact_full_sequence


@pytest.mark.parametrize("residue", ["B", "J", "X", "Z"])
def test_iupac26_ambiguities_are_not_wildcards(residue: str) -> None:
    row = replace(
        BASE,
        target_sequence=SEQ + residue,
        candidate_sequence=SEQ + "A",
    )
    result = assess_strain_gate(row, policy="same_species_exact")
    assert result.status == "DENIED"
    assert result.reason == "NONIDENTICAL_FULL_SEQUENCE"
    assert not result.exact_full_sequence


@pytest.mark.parametrize("invalid", ["*", "?", "-", "a"])
def test_invalid_alphabet_exact_sequences_are_denied(invalid: str) -> None:
    row = replace(
        BASE,
        target_sequence=SEQ + invalid,
        candidate_sequence=SEQ + invalid,
    )
    result = assess_strain_gate(row, policy="same_species_exact")
    assert result.status == "DENIED"
    assert result.reason == "INVALID_PROTEIN_ALPHABET"
    assert not result.exact_full_sequence


def test_biologically_explicit_special_residues_are_allowed() -> None:
    row = replace(BASE, target_sequence=SEQ + "UO", candidate_sequence=SEQ + "UO")
    assert assess_strain_gate(row, policy="same_species_exact").status == "ELIGIBLE"


def test_unknown_policy_is_error() -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        assess_strain_gate(BASE, policy="include_audited_same_species")
