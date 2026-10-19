"""Stage 2C cross-strain donor policy over verified Stage 07 evidence.

Preserve Stage 07 decisions outside the cross-subtaxon exact gate. Under
same_species_exact, literal IUPAC full-sequence identity and verified
same-species taxonomy suffice without additional locus evidence.
Fragment-specific safeguards remain independent.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Literal

from probe.cross_strain import (
    StrainGateInput,
    assess_strain_gate,
    literal_protein,
)
from probe.resolver import (
    ResolverDecision,
    ResolverPolicy,
    apply_accepted_multiplicity,
    stable_unique_locus,
)
from probe.resolver_alignments import JoinedAlignment
from probe.resolver_assembly import (
    ResolvedCandidate,
    resolve_joined_candidates,
    validate_policy_skipped_candidate,
)
from probe.resolver_candidates import CandidateKey, CandidateProvenance

StrainPolicy = Literal["strict", "same_species_exact"]
TaxonContext = tuple[str | None, str | None]
SequencePair = tuple[str, str]


@dataclass(frozen=True)
class VariantResult:
    """All candidates are retained, even when admission is denied."""

    rows: dict[CandidateKey, ResolvedCandidate]
    cross_strain_total: int
    cross_strain_donors: int


def _documentary_fragment(fact: CandidateProvenance) -> bool:
    """Only independently audited anchors or verified locus lineage qualify."""
    if fact.direct.audited_direct_verified:
        return True
    lineage = fact.lineage
    if lineage is None:
        return False
    return stable_unique_locus(lineage.matched_locus_keys, taxonomy_pass=True)


def _override(
    previous: ResolvedCandidate,
    *,
    decision: str,
    basis: str,
    warning: str,
    donor: bool,
) -> ResolvedCandidate:
    old = previous.decision
    warnings = set(old.warnings)
    if decision == "ACCEPTED":
        warnings.difference_update(
            {
                "EXACT_SEQUENCE_WITHOUT_STRONG_DOCUMENTARY_CONTINUITY",
                "EXACT_SEQUENCE_WITH_ONLY_WEAK_DOCUMENTARY_SUPPORT",
                "SAME_SPECIES_WRONG_SUBTAXON",
            }
        )
    new = replace(
        old,
        evaluation_scope=("TAXONOMY_AUDIT" if decision == "AUDIT_ONLY" else "PRIMARY"),
        decision=decision,
        decision_basis=basis,
        entity_equivalence="NOT_ASSERTED",
        annotation_donor=donor,
        warnings=tuple(sorted(warnings | {warning})),
        audit_outcome=(
            "EVALUATED_UNDER_CROSS_STRAIN_EXACT_POLICY"
            if decision == "ACCEPTED"
            and (
                old.source_scope == "TAXONOMY_AUDIT"
                or previous.candidate.overlap_audit is not None
            )
            else (
                "EXCLUDED_FROM_CROSS_STRAIN_POLICY"
                if decision == "AUDIT_ONLY"
                else None
            )
        ),
    )
    return replace(previous, decision=new)


def resolve_stage2c_variant(
    *,
    provenance: Mapping[CandidateKey, CandidateProvenance],
    alignments: Mapping[CandidateKey, JoinedAlignment],
    taxonomy: Mapping[tuple[str, str], TaxonContext],
    sequences: Mapping[CandidateKey, SequencePair],
    release_order: tuple[str, str],
    strain_policy: StrainPolicy,
    partial_sequence: str = "allow",
    documentary_fragment: str = "allow",
) -> VariantResult:
    """Resolve the same discovery twice without changing Stage 07 v1.

    ``taxonomy`` must come from verified Stage 02B, not FASTA header TaxIDs.
    ``sequences`` must come from checked Stage 01/05D complete sequences.
    Stage 06A/06C1 exactness is independently reconciled with those bytes.
    """
    if strain_policy not in {"strict", "same_species_exact"}:
        raise ValueError("Unsupported Stage 2C strain policy")
    policy = ResolverPolicy(
        subtaxon_policy="strict",
        partial_sequence=partial_sequence,
        documentary_fragment=documentary_fragment,
    )
    if set(provenance) != set(alignments):
        raise ValueError("Stage 2C candidate/alignment coverage mismatch")
    skipped = {key for key, aln in alignments.items() if aln.policy_skipped}
    for key in skipped:
        validate_policy_skipped_candidate(provenance[key], alignments[key])
    eligible = set(provenance) - skipped
    baseline = resolve_joined_candidates(
        provenance={key: provenance[key] for key in eligible},
        alignments={key: alignments[key] for key in eligible},
        release_order=release_order,
        policy=policy,
    )
    current = dict(baseline)
    cross_total = 0
    cross_donors = 0
    for key, fact in provenance.items():
        candidate = fact.candidate
        direct = fact.direct
        context = taxonomy.get(key[:2])
        if context is None:
            raise ValueError(f"Missing verified Stage 02B target: {key[:2]}")
        target_taxid, target_species = context
        if direct.target_resolved_taxid != target_taxid:
            raise ValueError(f"Stage 02B/04B target TaxID conflict: {key}")
        candidate_taxid = candidate.candidate_resolved_taxid
        if direct.candidate_resolved_taxid != candidate_taxid:
            raise ValueError(f"Stage 02B/04B candidate TaxID conflict: {key}")
        if key in skipped:
            if (
                target_taxid is None
                or candidate_taxid is None
                or target_taxid == candidate_taxid
            ):
                raise ValueError(
                    f"Policy-skipped pair lacks verified cross-TaxID: {key}"
                )
            cross_total += 1
            audit_only = candidate.scope == "TAXONOMY_AUDIT"
            decision = ResolverDecision(
                source_scope=candidate.scope,
                evaluation_scope=("TAXONOMY_AUDIT" if audit_only else "PRIMARY"),
                decision="AUDIT_ONLY" if audit_only else "UNRESOLVED",
                decision_basis="CROSS_STRAIN_NONEXACT_POLICY_EXCLUDED",
                entity_equivalence="NOT_ASSERTED",
                annotation_donor=False,
                audit_outcome=(
                    "EXCLUDED_FROM_CROSS_STRAIN_POLICY" if audit_only else None
                ),
                warnings=("CROSS_STRAIN_NONEXACT_NO_DP",),
            )
            current[key] = ResolvedCandidate(
                candidate=candidate,
                decision=decision,
                sequence_relation="NOT_ALIGNED_BY_POLICY",
                exact_sequence=None,
            )
            continue
        if target_taxid is not None and target_taxid == candidate_taxid:
            aln = alignments[key]
            if (
                candidate.scope == "PRIMARY"
                and aln.aligned
                and aln.exact_sequence is True
            ):
                pair = sequences.get(key)
                if pair is None:
                    raise ValueError(f"Missing literal same-TaxID proof: {key}")
                target_sequence, candidate_sequence = pair
                if not target_sequence or target_sequence != candidate_sequence:
                    raise ValueError(f"Stage 06A/literal sequence conflict: {key}")
                if (
                    current[key].decision.decision != "ACCEPTED"
                    and literal_protein(target_sequence)
                    and (not candidate.fragment_record or _documentary_fragment(fact))
                ):
                    current[key] = _override(
                        current[key],
                        decision="ACCEPTED",
                        basis="SAME_TAXID_LITERAL_EXACT_SEQUENCE",
                        warning="SAME_TAXID_LITERAL_EXACT_PROOF",
                        donor=(
                            not candidate.fragment_record
                            or documentary_fragment == "allow"
                        ),
                    )
            continue
        audit_only = candidate.scope == "TAXONOMY_AUDIT"
        denied_decision = "AUDIT_ONLY" if audit_only else "UNRESOLVED"
        # A missing TaxID cannot qualify even if canonical documentary
        # evidence would otherwise accept this candidate.
        if target_taxid is None or candidate_taxid is None:
            current[key] = _override(
                current[key],
                decision=denied_decision,
                basis="CROSS_STRAIN_UNVERIFIED_TAXONOMY",
                warning="UNRESOLVED_TAXONOMY",
                donor=False,
            )
            continue
        cross_total += 1
        species = candidate.candidate_species_taxid
        if not target_species or not species or species != target_species:
            current[key] = _override(
                current[key],
                decision=denied_decision,
                basis="CROSS_STRAIN_TAXONOMY_DENIED",
                warning="CROSS_SPECIES_OR_UNVERIFIED_SPECIES",
                donor=False,
            )
            continue
        if strain_policy == "strict":
            current[key] = _override(
                current[key],
                decision=denied_decision,
                basis="CROSS_STRAIN_STRICT_POLICY",
                warning="CROSS_STRAIN_NOT_ELIGIBLE_UNDER_STRICT",
                donor=False,
            )
            continue
        aln = alignments[key]
        if aln.exact_sequence is None or not aln.aligned:
            raise ValueError(f"Cross-strain candidate is not aligned: {key}")
        pair = sequences.get(key)
        if pair is None:
            raise ValueError(f"Missing literal cross-strain sequence proof: {key}")
        target_sequence, candidate_sequence = pair
        if (target_sequence == candidate_sequence) != aln.exact_sequence:
            raise ValueError(f"Stage 06A/literal sequence conflict: {key}")
        gate = assess_strain_gate(
            StrainGateInput(
                target_taxid=target_taxid,
                candidate_taxid=candidate_taxid,
                target_species_taxid=target_species,
                candidate_species_taxid=species,
                target_sequence=target_sequence,
                candidate_sequence=candidate_sequence,
                fragment_record=candidate.fragment_record,
                verified_documentary_fragment=_documentary_fragment(fact),
            ),
            policy=strain_policy,
        )
        if gate.status == "ELIGIBLE":
            # Verified same-species full identity is the cross-subtaxon
            # admission evidence; no additional locus/lineage gate.
            donor = not candidate.fragment_record or documentary_fragment == "allow"
            current[key] = _override(
                current[key],
                decision="ACCEPTED",
                basis="SAME_SPECIES_LITERAL_EXACT_SEQUENCE",
                warning="CROSS_STRAIN_LITERAL_EXACT_PROOF",
                donor=donor,
            )
            cross_donors += int(donor)
        else:
            current[key] = _override(
                current[key],
                decision=denied_decision,
                basis="CROSS_STRAIN_DENIED_" + gate.reason,
                warning=gate.reason,
                donor=False,
            )
    # The initial canonical pass calculated counts before cross-strain
    # exclusions. Remove its warning before recalculating the final counts.
    counts = apply_accepted_multiplicity(
        [
            (
                *key,
                replace(
                    row.decision,
                    accepted_candidates_for_target_release=0,
                    warnings=tuple(
                        warning
                        for warning in row.decision.warnings
                        if warning != "MULTIPLE_ACCEPTED_CANDIDATES"
                    ),
                ),
            )
            for key, row in current.items()
        ]
    )
    final = {
        (release, target, rid): replace(
            current[(release, target, rid)], decision=decision
        )
        for release, target, rid, decision in counts
    }
    if set(final) != set(provenance):
        raise ValueError("Resolver variant lost candidate coverage")
    return VariantResult(final, cross_total, cross_donors)
