"""Assemble Stage 07 decisions from independently validated upstream facts.

No candidate discovery, alignment, ranking, config I/O, or production writes.
The caller must use the strict Stage 07 candidate, anchor, lineage and
alignment readers before passing these typed records to this module.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from probe.resolver import (
    ResolverDecision,
    ResolverEvidence,
    ResolverPolicy,
    apply_accepted_multiplicity,
    decide,
    sequence_relation,
    stable_unique_locus,
)
from probe.resolver_alignments import JoinedAlignment
from probe.resolver_candidates import (
    CandidateKey,
    CandidateProvenance,
    CandidateRecord,
)

STAGE07_COLUMNS = (
    "release",
    "target_id",
    "rid",
    "scope",
    "evaluation_scope",
    "target_accession",
    "candidate_accession",
    "exact_sequence",
    "decision",
    "decision_basis",
    "entity_equivalence",
    "annotation_donor",
    "sequence_relation",
    "accepted_candidates_for_target_release",
    "warnings",
    "audit_class",
    "audit_reason",
    "audit_alignment_allowed",
    "audit_outcome",
)


def _order(key: CandidateKey, releases: tuple[str, str]) -> tuple[int, str, int]:
    if key[0] not in releases:
        raise ValueError(f"Unexpected Stage 07 release: {key[0]}")
    if not key[2].isascii() or not key[2].isdecimal():
        raise ValueError(f"Invalid release-scoped record ID: {key[2]!r}")
    if int(key[2]) < 1 or not key[1]:
        raise ValueError(f"Invalid Stage 07 candidate key: {key}")
    return (releases.index(key[0]), key[1], int(key[2]))


def _validate_provenance(
    facts: CandidateProvenance,
    alignment: JoinedAlignment,
) -> None:
    candidate = facts.candidate
    direct = facts.direct
    lineage = facts.lineage
    audit = candidate.audit or candidate.overlap_audit
    key = candidate.key

    if direct.candidate_resolved_taxid != candidate.candidate_resolved_taxid:
        raise ValueError(f"{key}: contradictory candidate resolved TaxID")
    equal_taxid = (
        direct.target_resolved_taxid is not None
        and direct.candidate_resolved_taxid is not None
        and direct.target_resolved_taxid == direct.candidate_resolved_taxid
    )
    if direct.exact_resolved_taxid != equal_taxid:
        raise ValueError(f"{key}: contradictory exact resolved TaxID")
    if direct.direct_taxonomy_pass and (
        candidate.scope != "PRIMARY" or direct.direct_match_type is None
    ):
        raise ValueError(f"{key}: invalid primary direct taxonomy gate")
    if direct.audited_direct_verified and (
        audit is None or audit.reason != "SAME_SPECIES_WRONG_SUBTAXON"
    ):
        raise ValueError(f"{key}: invalid audited direct provenance")
    if audit is not None and direct.direct_match_type != audit.direct_match_type:
        raise ValueError(f"{key}: audited direct accession type mismatch")
    if lineage is None:
        if candidate.lineage_discovered:
            raise ValueError(f"{key}: missing 04D2 documentary evidence")
    else:
        if candidate.scope != "PRIMARY" or not candidate.lineage_discovered:
            raise ValueError(f"{key}: unexpected documentary lineage")
        if len(lineage.matched_typed_keys) != candidate.lineage_key_count:
            raise ValueError(f"{key}: 04D2 documentary key count mismatch")
        if lineage.collision_present != candidate.lineage_collision_present:
            raise ValueError(f"{key}: 04D2 documentary collision mismatch")
    if candidate.scope == "PRIMARY":
        if not alignment.policy_skipped and (
            not alignment.aligned or alignment.exact_sequence is None
        ):
            raise ValueError(f"{key}: missing PRIMARY canonical alignment")
    else:
        if audit is None:
            raise ValueError(f"{key}: missing immutable audit provenance")
        if (
            alignment.aligned != audit.alignment_allowed
            and not alignment.policy_skipped
        ):
            raise ValueError(f"{key}: audit alignment policy mismatch")
        if not alignment.aligned and alignment.exact_sequence is not None:
            raise ValueError(f"{key}: unexpected unaligned exactness")
        if audit.reason == "SAME_SPECIES_WRONG_SUBTAXON":
            if not direct.audited_direct_verified:
                raise ValueError(f"{key}: unverified audited direct anchor")
        elif audit.reason != "CROSS_SPECIES":
            raise ValueError(f"{key}: unknown taxonomy audit reason")
    if alignment.policy_skipped and (
        alignment.aligned
        or alignment.exact_sequence is not None
        or alignment.target_global is not None
        or alignment.candidate_global is not None
        or alignment.exact_subsequence
        or alignment.equal_length_variant
    ):
        raise ValueError(f"{key}: invalid policy-skipped alignment")
    if alignment.aligned and alignment.exact_sequence is None:
        raise ValueError(f"{key}: missing aligned literal exactness")
    if not alignment.aligned and (
        alignment.exact_subsequence or alignment.equal_length_variant
    ):
        raise ValueError(f"{key}: geometry on unaligned candidate")

    return None


def _evidence(
    facts: CandidateProvenance,
    alignment: JoinedAlignment,
) -> ResolverEvidence:
    _validate_provenance(facts, alignment)
    candidate = facts.candidate
    direct = facts.direct
    lineage = facts.lineage
    audit = candidate.audit if candidate.scope == "TAXONOMY_AUDIT" else None
    locus_keys = () if lineage is None else lineage.matched_locus_keys
    stable = stable_unique_locus(
        locus_keys,
        taxonomy_pass=candidate.scope == "PRIMARY",
    )
    return ResolverEvidence(
        scope=candidate.scope,
        exact_sequence=alignment.exact_sequence,
        audit_reason=None if audit is None else audit.reason,
        audit_alignment_allowed=(None if audit is None else audit.alignment_allowed),
        direct_match_type=direct.direct_match_type,
        direct_taxonomy_pass=direct.direct_taxonomy_pass,
        audited_direct_verified=(
            direct.audited_direct_verified
            and candidate.scope == "TAXONOMY_AUDIT"
        ),
        nondirect_accession=(lineage is not None and lineage.accession_key_present),
        stable_locus=stable,
        exact_resolved_taxid=direct.exact_resolved_taxid,
        exact_subsequence=alignment.exact_subsequence,
        equal_length_variant=alignment.equal_length_variant,
        lineage_present=lineage is not None,
        fragment_record=candidate.fragment_record,
        collision_present=(False if lineage is None else lineage.collision_present),
    )


@dataclass(frozen=True, slots=True)
class ResolvedCandidate:
    """Original pair metadata, scientific decision, and descriptive geometry."""

    candidate: CandidateRecord
    decision: ResolverDecision
    sequence_relation: str
    exact_sequence: bool | None

    @property
    def key(self) -> CandidateKey:
        return self.candidate.key

    def tsv_row(self) -> dict[str, str]:
        """Return exactly the proposed 19-column Stage 07 schema."""
        candidate = self.candidate
        decision = self.decision
        audit = candidate.audit or candidate.overlap_audit
        exact = self.exact_sequence
        row = {
            "release": candidate.release,
            "target_id": candidate.target_id,
            "rid": candidate.rid,
            "scope": candidate.scope,
            "evaluation_scope": decision.evaluation_scope,
            "target_accession": candidate.target_accession or ".",
            "candidate_accession": candidate.candidate_accession,
            "exact_sequence": "." if exact is None else str(int(exact)),
            "decision": decision.decision,
            "decision_basis": decision.decision_basis,
            "entity_equivalence": decision.entity_equivalence,
            "annotation_donor": ("YES" if decision.annotation_donor else "NO"),
            "sequence_relation": self.sequence_relation,
            "accepted_candidates_for_target_release": (
                "."
                if decision.decision == "AUDIT_ONLY"
                else str(decision.accepted_candidates_for_target_release)
            ),
            "warnings": ",".join(decision.warnings) or ".",
            "audit_class": "." if audit is None else audit.audit_class,
            "audit_reason": "." if audit is None else audit.reason,
            "audit_alignment_allowed": (
                "." if audit is None else str(int(audit.alignment_allowed))
            ),
            "audit_outcome": decision.audit_outcome or ".",
        }
        if tuple(row) != STAGE07_COLUMNS:
            raise ValueError("Unexpected Stage 07 output column contract")
        return row


def resolve_joined_candidates(
    *,
    provenance: Mapping[CandidateKey, CandidateProvenance],
    alignments: Mapping[CandidateKey, JoinedAlignment],
    release_order: tuple[str, str],
    policy: ResolverPolicy | None = None,
) -> dict[CandidateKey, ResolvedCandidate]:
    """Resolve every upstream pair once, never choosing a best accession.

    Release order comes from validated START and END release configuration.
    05A2+05C2 key coverage is mandatory, including forbidden-to-align audits.
    No scientific or data-dependent cutoff is used.
    """
    if len(release_order) != 2 or len(set(release_order)) != 2:
        raise ValueError("Expected two distinct concrete release IDs")
    if not all(release_order):
        raise ValueError("Empty START or END release ID")
    if set(provenance) != set(alignments):
        missing = sorted(set(provenance) - set(alignments))[:3]
        extra = sorted(set(alignments) - set(provenance))[:3]
        raise ValueError(f"Stage 07 alignment coverage mismatch: {missing}, {extra}")
    if policy is None:
        policy = ResolverPolicy()
    ordered_keys = sorted(provenance, key=lambda k: _order(k, release_order))
    initial: dict[CandidateKey, ResolvedCandidate] = {}
    decisions: list[tuple[str, str, str, ResolverDecision]] = []
    for key in ordered_keys:
        facts = provenance[key]
        if key != facts.candidate.key:
            raise ValueError(f"{key}: mismatched candidate provenance key")
        aln = alignments[key]
        if aln.policy_skipped:
            raise ValueError(f"{key}: skipped pair requires Stage 2C gate")
        evidence = _evidence(facts, aln)
        decision = decide(evidence, policy)
        relation = sequence_relation(
            exact_sequence=aln.exact_sequence,
            aligned=aln.aligned,
            decision_basis=decision.decision_basis,
            go_positive_sequence_only=(
                facts.candidate.go_positive_sequence_discovered
                and not facts.candidate.full_v2_rescue_sequence_discovered
                and not facts.candidate.lineage_discovered
            ),
            exact_subsequence=aln.exact_subsequence,
            equal_length_variant=aln.equal_length_variant,
        )
        initial[key] = ResolvedCandidate(
            facts.candidate, decision, relation, aln.exact_sequence
        )
        decisions.append((*key, decision))
    with_counts = apply_accepted_multiplicity(decisions)
    result = {}
    for release, target_id, rid, decision in with_counts:
        key = (release, target_id, rid)
        old = initial[key]
        result[key] = ResolvedCandidate(
            old.candidate, decision, old.sequence_relation, old.exact_sequence
        )
    if set(result) != set(provenance):
        raise ValueError("Stage 07 decision coverage mismatch")
    return result


def validate_policy_skipped_candidate(
    facts: CandidateProvenance, alignment: JoinedAlignment
) -> None:
    """Validate documentary invariants without creating an alignment result."""
    if not alignment.policy_skipped:
        raise ValueError("Expected explicitly policy-skipped candidate")
    _validate_provenance(facts, alignment)
