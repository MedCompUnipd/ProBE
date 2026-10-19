"""Stage 2C V1 taxonomy/sequence gate; not yet wired to Stage 07.

This is a pre-donation eligibility gate, not an annotation decision.
Both taxonomic resolutions must come from a verified release snapshot.
An exact accession string or sequence digest is never taxonomy evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

StrainPolicy = Literal["strict", "same_species_exact"]
GateStatus = Literal["DENIED", "ELIGIBLE", "DEFER_TO_DOCUMENTARY"]

# X, B, Z and J are ambiguous; '*' indicates termination, not a residue.
# U (selenocysteine) and O (pyrrolysine) are explicit residues.
_RESIDUES = frozenset("ACDEFGHIKLMNPQRSTVWYOU")
_LITERAL_RESIDUES = _RESIDUES | frozenset("BJXZ")
LITERAL_EXACT_POLICY = "literal-iupac26-v1"


@dataclass(frozen=True)
class StrainGateInput:
    target_taxid: str
    candidate_taxid: str
    target_species_taxid: str
    candidate_species_taxid: str
    target_sequence: str
    candidate_sequence: str
    fragment_record: bool = False
    verified_documentary_fragment: bool = False


@dataclass(frozen=True)
class StrainGateResult:
    status: GateStatus
    reason: str
    exact_full_sequence: bool


def unambiguous_protein(sequence: str) -> bool:
    """True only for a nonempty protein with fully specified residues."""
    return bool(sequence) and set(sequence).issubset(_RESIDUES)


def literal_protein(sequence: str) -> bool:
    """Accept literal IUPAC protein symbols, never ambiguity expansion."""
    return bool(sequence) and set(sequence).issubset(_LITERAL_RESIDUES)


def _valid_taxid(value: str) -> bool:
    return bool(value) and value.isascii() and value.isdecimal() and int(value) > 0


def assess_strain_gate(
    evidence: StrainGateInput,
    *,
    policy: StrainPolicy,
) -> StrainGateResult:
    """Decide only the additional cross-strain eligibility conditions.

    ``DEFER_TO_DOCUMENTARY`` preserves existing same-TaxID resolver rules.
    ``ELIGIBLE`` is NOT automatic GO transfer: downstream guards still apply.
    ``DENIED`` must not become a donor via another cross-strain rule.
    """
    if policy not in {"strict", "same_species_exact"}:
        raise ValueError("Unsupported 2C strain policy")

    ids = (
        evidence.target_taxid,
        evidence.candidate_taxid,
        evidence.target_species_taxid,
        evidence.candidate_species_taxid,
    )
    if not all(_valid_taxid(value) for value in ids):
        return StrainGateResult("DENIED", "UNRESOLVED_TAXONOMY", False)

    if evidence.target_species_taxid != evidence.candidate_species_taxid:
        return StrainGateResult("DENIED", "CROSS_SPECIES", False)

    if evidence.target_taxid == evidence.candidate_taxid:
        return StrainGateResult("DEFER_TO_DOCUMENTARY", "SAME_RESOLVED_TAXID", False)

    if policy == "strict":
        return StrainGateResult("DENIED", "STRICT_CROSS_STRAIN", False)

    target = evidence.target_sequence
    candidate = evidence.candidate_sequence
    if not target or not candidate or target != candidate:
        return StrainGateResult("DENIED", "NONIDENTICAL_FULL_SEQUENCE", False)

    if not literal_protein(target):
        return StrainGateResult("DENIED", "INVALID_PROTEIN_ALPHABET", False)

    if evidence.fragment_record and not evidence.verified_documentary_fragment:
        return StrainGateResult("DENIED", "FRAGMENT_NEEDS_DOCUMENTARY", True)

    return StrainGateResult("ELIGIBLE", "EXACT_SAME_SPECIES", True)
