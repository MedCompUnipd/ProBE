"""Stage 07 pure decision logic (implementation increment 1).

This module has no I/O, no candidate discovery, no ranking, and no numerical
sequence-similarity thresholds. Source evidence must be validated separately
before being converted to the typed inputs defined here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Literal
from urllib.parse import unquote

DecisionName = Literal["ACCEPTED", "UNRESOLVED", "AUDIT_ONLY"]
SubtaxonPolicy = Literal["strict", "include_audited_same_species"]
DonorPolicy = Literal["allow", "report_only"]


@dataclass(frozen=True)
class ResolverPolicy:
    subtaxon_policy: SubtaxonPolicy = "strict"
    partial_sequence: DonorPolicy = "allow"
    documentary_fragment: DonorPolicy = "allow"

    def __post_init__(self) -> None:
        if self.subtaxon_policy not in {"strict", "include_audited_same_species"}:
            raise ValueError("Invalid Stage 07 subtaxon policy")
        if self.partial_sequence not in {"allow", "report_only"}:
            raise ValueError("Invalid Stage 07 partial-sequence policy")
        if self.documentary_fragment not in {"allow", "report_only"}:
            raise ValueError("Invalid Stage 07 documentary-fragment policy")


@dataclass(frozen=True)
class MatchedLocusKey:
    """One matched 04D2 typed key, cross-checked with authoritative 04D1."""

    key_type: str
    key_value: str
    from_start_anchor: bool
    from_end_anchor: bool
    key_collision: bool
    index_verified: bool

    def __post_init__(self) -> None:
        if self.key_type not in {"ORDERED_LOCUS", "ORF"}:
            raise ValueError("A locus key must be ORDERED_LOCUS or ORF")
        if not self.key_value:
            raise ValueError("An empty locus key is invalid")
        for name in (
            "from_start_anchor",
            "from_end_anchor",
            "key_collision",
            "index_verified",
        ):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be boolean")


_SO_TERM = re.compile(r"SO:[0-9]{7}\Z", re.ASCII)


def is_known_nonidentifying_locus(key_type: str, key_value: str) -> bool:
    """Version 1: exclude recognizable Sequence Ontology terms, not ORF names.

    Percent-decoding is for ontology-term recognition only.
    All 04D1/04D2 joins preserve the literal key; no fanout cutoff.
    The caller must supply literal scalar key values from 04D2/04D1.
    """
    return key_type in {"ORDERED_LOCUS", "ORF"} and bool(
        _SO_TERM.fullmatch(unquote(key_value))
    )


def stable_unique_locus(
    keys: tuple[MatchedLocusKey, ...], *, taxonomy_pass: bool
) -> bool:
    """Use an individually matched, noncolliding, START+END-supported typed key."""
    if type(taxonomy_pass) is not bool:
        raise ValueError("taxonomy_pass must be boolean")
    if not taxonomy_pass:
        return False
    return any(
        key.index_verified
        and key.from_start_anchor
        and key.from_end_anchor
        and not key.key_collision
        and not is_known_nonidentifying_locus(key.key_type, key.key_value)
        for key in keys
    )


@dataclass(frozen=True)
class ResolverEvidence:
    """Validated evidence for a single (release, target_id, rid) triple.

    `direct_match_type` must have been independently verified against 04B.
    `direct_taxonomy_pass` is the ordinary primary gate. For an audited pair,
    `audited_direct_verified` certifies a separately validated direct anchor
    under the explicit optional policy; never infer it from equal strings.
    `exact_subsequence` and `equal_length_variant` are proven 06C1 predicates,
    not score/identity estimates.
    """

    scope: Literal["PRIMARY", "TAXONOMY_AUDIT"]
    exact_sequence: bool | None
    audit_reason: str | None = None
    audit_alignment_allowed: bool | None = None
    direct_match_type: Literal["PRIMARY", "SECONDARY"] | None = None
    direct_taxonomy_pass: bool = False
    audited_direct_verified: bool = False
    nondirect_accession: bool = False
    stable_locus: bool = False
    exact_resolved_taxid: bool = False
    exact_subsequence: bool = False
    equal_length_variant: bool = False
    lineage_present: bool = False
    fragment_record: bool = False
    collision_present: bool = False

    def __post_init__(self) -> None:
        if self.scope not in {"PRIMARY", "TAXONOMY_AUDIT"}:
            raise ValueError("Unknown scope")
        if self.scope == "PRIMARY":
            if (
                self.audit_reason is not None
                or self.audit_alignment_allowed is not None
            ):
                raise ValueError("Primary pairs cannot carry audit reason/permission")
            if type(self.exact_sequence) is not bool:
                raise ValueError("Primary pairs require validated literal exactness")
            if self.audited_direct_verified:
                raise ValueError(
                    "Primary pairs cannot use audited direct-anchor override"
                )
        else:
            if self.audit_reason not in {
                "CROSS_SPECIES",
                "SAME_SPECIES_WRONG_SUBTAXON",
            }:
                raise ValueError("Unknown taxonomy audit reason")
            if type(self.audit_alignment_allowed) is not bool:
                raise ValueError("Audit alignment permission must be boolean")
            if self.audit_reason == "CROSS_SPECIES":
                if self.audit_alignment_allowed or self.exact_sequence is not None:
                    raise ValueError("Cross-species audit cannot be aligned")
                if self.audited_direct_verified:
                    raise ValueError("Cross-species audit cannot be promoted")
            elif (
                not self.audit_alignment_allowed
                or type(self.exact_sequence) is not bool
            ):
                raise ValueError("Same-species audit must have validated alignment")
        if self.direct_match_type not in {None, "PRIMARY", "SECONDARY"}:
            raise ValueError("Unknown direct accession match type")
        for name in (
            "direct_taxonomy_pass",
            "audited_direct_verified",
            "nondirect_accession",
            "stable_locus",
            "exact_resolved_taxid",
            "exact_subsequence",
            "equal_length_variant",
            "lineage_present",
            "fragment_record",
            "collision_present",
        ):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be boolean")
        if self.audited_direct_verified and self.direct_match_type is None:
            raise ValueError(
                "Audited override requires verified PRIMARY/SECONDARY anchor"
            )
        if self.exact_sequence is True and (
            self.exact_subsequence or self.equal_length_variant
        ):
            raise ValueError("Exact pairs cannot have nonexact sequence geometry")
        if self.exact_subsequence and self.equal_length_variant:
            raise ValueError("Incompatible positive nonexact geometries")


@dataclass(frozen=True)
class ResolverDecision:
    source_scope: str
    evaluation_scope: str
    decision: DecisionName
    decision_basis: str
    entity_equivalence: str
    annotation_donor: bool
    audit_outcome: str | None
    warnings: tuple[str, ...]
    accepted_candidates_for_target_release: int = 0


def _result(
    evidence: ResolverEvidence,
    policy: ResolverPolicy,
    *,
    evaluation_scope: str,
    decision: DecisionName,
    basis: str,
    equivalence: str,
    audit_outcome: str | None,
    core_warnings: tuple[str, ...] = (),
) -> ResolverDecision:
    tokens = list(core_warnings)
    if evidence.fragment_record and evaluation_scope == "PRIMARY":
        tokens.append("FRAGMENT_RECORD")
    if evidence.collision_present and evaluation_scope == "PRIMARY":
        tokens.append("COLLIDING_DOCUMENTARY_KEY_PRESENT")
    donor = decision == "ACCEPTED"
    if donor and basis == "STABLE_UNIQUE_LOCUS_EXACT_SUBSEQUENCE":
        donor = policy.partial_sequence == "allow"
    if donor and evidence.fragment_record:
        donor = policy.documentary_fragment == "allow"
    return ResolverDecision(
        source_scope=evidence.scope,
        evaluation_scope=evaluation_scope,
        decision=decision,
        decision_basis=basis,
        entity_equivalence=equivalence,
        annotation_donor=donor,
        audit_outcome=audit_outcome,
        warnings=tuple(sorted(set(tokens))),
    )


def decide(
    evidence: ResolverEvidence, policy: ResolverPolicy | None = None
) -> ResolverDecision:
    """Implement frozen v1 scientific precedence on already-validated evidence."""
    if policy is None:
        policy = ResolverPolicy()
    audit_outcome = None
    if evidence.scope == "TAXONOMY_AUDIT":
        if evidence.audit_reason == "CROSS_SPECIES":
            return _result(
                evidence,
                policy,
                evaluation_scope="TAXONOMY_AUDIT",
                decision="AUDIT_ONLY",
                basis="TAXONOMY_AUDIT",
                equivalence="NOT_APPLICABLE",
                audit_outcome="REJECTED_BY_TAXONOMY_POLICY",
                core_warnings=("CROSS_SPECIES",),
            )
        if policy.subtaxon_policy == "strict":
            return _result(
                evidence,
                policy,
                evaluation_scope="TAXONOMY_AUDIT",
                decision="AUDIT_ONLY",
                basis="TAXONOMY_AUDIT",
                equivalence="NOT_APPLICABLE",
                audit_outcome="EXCLUDED_FROM_PRIMARY_BY_SUBTAXON_POLICY",
                core_warnings=("SAME_SPECIES_WRONG_SUBTAXON",),
            )
        if not evidence.audited_direct_verified:
            raise ValueError(
                "Promoted audit pair requires independent verified direct anchor"
            )
        audit_outcome = "EVALUATED_UNDER_AUDITED_SUBTAXON_POLICY"

    def emit(
        decision: DecisionName, basis: str, equivalence: str, warning: str | None = None
    ) -> ResolverDecision:
        return _result(
            evidence,
            policy,
            evaluation_scope="PRIMARY",
            decision=decision,
            basis=basis,
            equivalence=equivalence,
            audit_outcome=audit_outcome,
            core_warnings=(warning,) if warning else (),
        )

    exact = evidence.exact_sequence
    direct = evidence.direct_match_type is not None and (
        evidence.direct_taxonomy_pass or evidence.audited_direct_verified
    )
    if direct:
        if exact:
            basis = (
                "DIRECT_PRIMARY_ACCESSION"
                if evidence.direct_match_type == "PRIMARY"
                else "DIRECT_SECONDARY_ACCESSION"
            )
            return emit("ACCEPTED", basis, "DOCUMENTARY_ACCESSION_CONTINUITY")
        return emit(
            "ACCEPTED",
            "DIRECT_ACCESSION_CONTINUITY",
            "DOCUMENTARY_ACCESSION_CONTINUITY",
            "ACCESSION_CONTINUITY_SEQUENCE_CHANGED",
        )
    if evidence.nondirect_accession and (exact or not evidence.stable_locus):
        if exact:
            return emit(
                "ACCEPTED", "DOCUMENTARY_ACCESSION_PLUS_EXACT_SEQUENCE", "NOT_ASSERTED"
            )
        return emit(
            "UNRESOLVED",
            "NONDIRECT_DOCUMENTARY_ACCESSION",
            "NOT_ASSERTED",
            "NONDIRECT_ACCESSION_WITH_NONEXACT_SEQUENCE",
        )
    if evidence.stable_locus:
        if exact:
            return emit(
                "ACCEPTED", "STABLE_UNIQUE_LOCUS_PLUS_EXACT_SEQUENCE", "NOT_ASSERTED"
            )
        if evidence.exact_resolved_taxid and evidence.exact_subsequence:
            return emit(
                "ACCEPTED",
                "STABLE_UNIQUE_LOCUS_EXACT_SUBSEQUENCE",
                "NOT_ASSERTED",
                "PARTIAL_SEQUENCE_EXACTLY_CONTAINED_IN_TARGET",
            )
        if evidence.exact_resolved_taxid and evidence.equal_length_variant:
            return emit(
                "ACCEPTED",
                "STABLE_UNIQUE_LOCUS_EQUAL_LENGTH_VARIANT",
                "NOT_ASSERTED",
                "SEQUENCE_VARIANT",
            )
        return emit(
            "UNRESOLVED",
            "STABLE_UNIQUE_LOCUS_OTHER_SEQUENCE_RELATION",
            "NOT_ASSERTED",
            "STABLE_LOCUS_SEQUENCE_RELATION_UNRESOLVED",
        )
    if exact:
        if evidence.lineage_present:
            return emit(
                "UNRESOLVED",
                "WEAK_OR_COLLIDING_DOCUMENTARY_PLUS_EXACT_SEQUENCE",
                "NOT_ASSERTED",
                "EXACT_SEQUENCE_WITH_ONLY_WEAK_DOCUMENTARY_SUPPORT",
            )
        return emit(
            "UNRESOLVED",
            "EXACT_SEQUENCE_ONLY",
            "NOT_ASSERTED",
            "EXACT_SEQUENCE_WITHOUT_STRONG_DOCUMENTARY_CONTINUITY",
        )
    if evidence.lineage_present:
        return emit(
            "UNRESOLVED",
            "WEAK_OR_COLLIDING_LINEAGE",
            "NOT_ASSERTED",
            "WEAK_OR_COLLIDING_LINEAGE",
        )
    return emit(
        "UNRESOLVED",
        "SEQUENCE_ONLY",
        "NOT_ASSERTED",
        "NO_STRONG_DOCUMENTARY_CONTINUITY",
    )


def apply_accepted_multiplicity(
    rows: list[tuple[str, str, str, ResolverDecision]],
) -> list[tuple[str, str, str, ResolverDecision]]:
    """Annotate every pair while preserving *all* candidates and their order.

    Input: list of (concrete release ID, target_id, rid, decision).
    Checks duplicate triple keys; no best-candidate selection.
    """
    seen: set[tuple[str, str, str]] = set()
    counts: dict[tuple[str, str], int] = {}
    for release, target_id, rid, decision in rows:
        key = (release, target_id, rid)
        if key in seen:
            raise ValueError("Duplicate candidate triple")
        seen.add(key)
        if decision.decision == "ACCEPTED":
            group = (release, target_id)
            counts[group] = counts.get(group, 0) + 1
    result = []
    for release, target_id, rid, decision in rows:
        count = counts.get((release, target_id), 0)
        warnings = set(decision.warnings)
        if decision.decision == "ACCEPTED" and count > 1:
            warnings.add("MULTIPLE_ACCEPTED_CANDIDATES")
        result.append(
            (
                release,
                target_id,
                rid,
                replace(
                    decision,
                    accepted_candidates_for_target_release=count,
                    warnings=tuple(sorted(warnings)),
                ),
            )
        )
    return result


def sequence_relation(
    *,
    exact_sequence: bool | None,
    aligned: bool,
    decision_basis: str,
    go_positive_sequence_only: bool,
    exact_subsequence: bool,
    equal_length_variant: bool,
) -> str:
    """Descriptive frozen dry-run vocabulary; not a decision predicate."""
    if type(aligned) is not bool or type(go_positive_sequence_only) is not bool:
        raise ValueError("Invalid alignment or discovery evidence")
    if not aligned:
        if exact_sequence is not None:
            raise ValueError("Unaligned audit must have missing exactness")
        return "NOT_ALIGNED_BY_POLICY"
    if type(exact_sequence) is not bool:
        raise ValueError("Aligned pairs require literal exactness")
    if exact_sequence:
        return "EXACT_SEQUENCE"
    if decision_basis == "SEQUENCE_ONLY" and go_positive_sequence_only:
        return "NONEXACT_SEQUENCE_ONLY"
    if exact_subsequence:
        return "CANDIDATE_EXACT_CONTIGUOUS_SUBSEQUENCE"
    if equal_length_variant:
        return "EQUAL_LENGTH_SUBSTITUTIONS_ONLY"
    return "OTHER_NONEXACT_RELATION"
