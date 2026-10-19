"""Canonical V2-only GO-donor gate for identical fragment-labelled records.

This does not modify frozen Stage 2C decisions, candidate discovery, or GOA.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from probe.resolver import apply_accepted_multiplicity
from probe.resolver_alignments import JoinedAlignment
from probe.resolver_candidates import CandidateKey, CandidateProvenance
from probe.stage2c_resolver_variant import VariantResult

# Same alphabet as target normalization, excluding the terminal stop marker.
_IUPAC_PROTEIN = frozenset("ACDEFGHIKLMNPQRSTVWYBJOUXZ")
_STALE_EXACT_WARNINGS = frozenset(
    {
        "EXACT_SEQUENCE_WITHOUT_STRONG_DOCUMENTARY_CONTINUITY",
        "EXACT_SEQUENCE_WITH_ONLY_WEAK_DOCUMENTARY_SUPPORT",
    }
)
DECISION_BASIS = "CANONICAL_SAME_TAXID_LITERAL_EXACT_FRAGMENT"


def promote_canonical_literal_fragments(
    *,
    result: VariantResult,
    provenance: Mapping[CandidateKey, CandidateProvenance],
    alignments: Mapping[CandidateKey, JoinedAlignment],
    sequences: Mapping[CandidateKey, tuple[str, str]],
    documentary_fragment: str,
    strain_policy: str,
    taxonomy: Mapping[tuple[str, str], tuple[str, str]],
) -> VariantResult:
    """Allow observed full-sequence identity, not biological completeness.

    Only PRIMARY, same verified resolved TaxID, true 06A literal EXACT,
    identical checked sequence bytes, and FRAG=1 are admitted by this rule.
    No claim of biological completeness is made. The donor receives only
    its actual release-local GO annotations, subject to normal GO filters.
    The existing report_only switch still prevents annotation donation.
    """
    if strain_policy not in {"strict", "same_species_exact"}:
        raise ValueError("Unknown cross-subtaxon EXACT policy")
    if documentary_fragment not in {"allow", "report_only"}:
        raise ValueError("Unknown fragment donor policy")
    if set(result.rows) != set(provenance) or set(result.rows) != set(alignments):
        raise ValueError("Incomplete canonical fragment evidence")
    updated = dict(result.rows)
    promoted = 0
    for key, item in result.rows.items():
        fact = provenance[key]
        candidate = fact.candidate
        direct = fact.direct
        alignment = alignments[key]
        target_taxid = direct.target_resolved_taxid
        candidate_taxid = candidate.candidate_resolved_taxid
        if (
            candidate.scope != "PRIMARY"
            or not candidate.fragment_record
            or target_taxid is None
            or candidate_taxid is None
            or direct.candidate_resolved_taxid != candidate_taxid
            or alignment.policy_skipped
            or not alignment.aligned
            or alignment.exact_sequence is not True
        ):
            continue
        tax = taxonomy.get(key[:2])
        if tax is None or tax[0] != target_taxid:
            raise ValueError(f"Unverified fragment target taxonomy: {key}")
        same_taxid = candidate_taxid == target_taxid
        if same_taxid != bool(direct.exact_resolved_taxid):
            raise ValueError(f"Contradictory resolved fragment TaxID: {key}")
        if not same_taxid and (
            strain_policy != "same_species_exact"
            or not tax[1]
            or tax[1] != candidate.candidate_species_taxid
        ):
            continue
        pair = sequences.get(key)
        if pair is None:
            raise ValueError(f"Missing canonical fragment sequence proof: {key}")
        target_seq, candidate_seq = pair
        if not target_seq or target_seq != candidate_seq:
            raise ValueError(f"Conflicting canonical fragment sequence: {key}")
        if not set(target_seq).issubset(_IUPAC_PROTEIN):
            raise ValueError(f"Invalid canonical fragment protein alphabet: {key}")
        old = item.decision
        if old.decision == "ACCEPTED":
            continue
        if old.decision != "UNRESOLVED":
            raise ValueError(f"Unexpected fragment admission state: {key}")
        warnings = (set(old.warnings) - _STALE_EXACT_WARNINGS) | {
            "FRAGMENT_RECORD",
            (
                "SAME_TAXID_LITERAL_EXACT_FRAGMENT_PROOF"
                if same_taxid
                else "SAME_SPECIES_LITERAL_EXACT_FRAGMENT_PROOF"
            ),
        }
        updated[key] = replace(
            item,
            decision=replace(
                old,
                evaluation_scope="PRIMARY",
                decision="ACCEPTED",
                decision_basis=(
                    DECISION_BASIS
                    if same_taxid
                    else "CANONICAL_SAME_SPECIES_LITERAL_EXACT_FRAGMENT"
                ),
                entity_equivalence="NOT_ASSERTED",
                annotation_donor=(documentary_fragment == "allow"),
                warnings=tuple(sorted(warnings)),
            ),
        )
        promoted += 1
    if not promoted:
        return result
    recalculated = apply_accepted_multiplicity(
        [
            (
                *key,
                replace(
                    item.decision,
                    accepted_candidates_for_target_release=0,
                    warnings=tuple(
                        warning
                        for warning in item.decision.warnings
                        if warning != "MULTIPLE_ACCEPTED_CANDIDATES"
                    ),
                ),
            )
            for key, item in updated.items()
        ]
    )
    for release, target, rid, decision in recalculated:
        key = release, target, rid
        updated[key] = replace(updated[key], decision=decision)
    cross_donors = sum(
        bool(item.decision.annotation_donor)
        for key, item in updated.items()
        if item.candidate.candidate_resolved_taxid != taxonomy[key[:2]][0]
    )
    return replace(
        result,
        rows=updated,
        cross_strain_donors=cross_donors,
    )
