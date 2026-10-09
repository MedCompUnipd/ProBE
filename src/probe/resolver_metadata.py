"""Versioned scientific metadata for Stage 07 association decisions."""

from __future__ import annotations

from probe.resolver import ResolverPolicy

SCHEMA_VERSION = "stage07-selection-metadata-v1"


# Frozen first-matching-rule precedence.
# Each entry: condition, decision, decision_basis.
RULE_PRECEDENCE = (
    (
        "audit_cross_species",
        "AUDIT_ONLY",
        "TAXONOMY_AUDIT",
    ),
    (
        "audit_wrong_subtaxon_and_strict_policy",
        "AUDIT_ONLY",
        "TAXONOMY_AUDIT",
    ),
    (
        "eligible_direct_primary_and_exact_sequence",
        "ACCEPTED",
        "DIRECT_PRIMARY_ACCESSION",
    ),
    (
        "eligible_direct_secondary_and_exact_sequence",
        "ACCEPTED",
        "DIRECT_SECONDARY_ACCESSION",
    ),
    (
        "eligible_direct_accession_and_nonexact_sequence",
        "ACCEPTED",
        "DIRECT_ACCESSION_CONTINUITY",
    ),
    (
        "nondirect_documentary_accession_and_exact_sequence",
        "ACCEPTED",
        "DOCUMENTARY_ACCESSION_PLUS_EXACT_SEQUENCE",
    ),
    (
        "nondirect_documentary_accession_nonexact_without_stable_locus",
        "UNRESOLVED",
        "NONDIRECT_DOCUMENTARY_ACCESSION",
    ),
    (
        "stable_unique_locus_and_exact_sequence",
        "ACCEPTED",
        "STABLE_UNIQUE_LOCUS_PLUS_EXACT_SEQUENCE",
    ),
    (
        "stable_unique_locus_exact_taxid_and_exact_candidate_subsequence",
        "ACCEPTED",
        "STABLE_UNIQUE_LOCUS_EXACT_SUBSEQUENCE",
    ),
    (
        "stable_unique_locus_exact_taxid_and_equal_length_variant",
        "ACCEPTED",
        "STABLE_UNIQUE_LOCUS_EQUAL_LENGTH_VARIANT",
    ),
    (
        "stable_unique_locus_with_other_nonexact_geometry",
        "UNRESOLVED",
        "STABLE_UNIQUE_LOCUS_OTHER_SEQUENCE_RELATION",
    ),
    (
        "exact_sequence_with_only_weak_or_colliding_documentary_evidence",
        "UNRESOLVED",
        "WEAK_OR_COLLIDING_DOCUMENTARY_PLUS_EXACT_SEQUENCE",
    ),
    (
        "exact_sequence_without_documentary_support",
        "UNRESOLVED",
        "EXACT_SEQUENCE_ONLY",
    ),
    (
        "nonexact_sequence_with_weak_or_colliding_lineage",
        "UNRESOLVED",
        "WEAK_OR_COLLIDING_LINEAGE",
    ),
    (
        "nonexact_sequence_without_strong_documentary_continuity",
        "UNRESOLVED",
        "SEQUENCE_ONLY",
    ),
)


def build_selection_metadata(
    *,
    start_release_id: str,
    end_release_id: str,
    policy: ResolverPolicy,
    policy_version: str,
) -> dict:
    """Describe effective Stage 07 rules without classifying candidates."""
    if not start_release_id or not end_release_id or start_release_id == end_release_id:
        raise ValueError("START and END release IDs must be distinct")

    if not isinstance(policy, ResolverPolicy):
        raise TypeError("policy must be a validated ResolverPolicy")

    if not policy_version:
        raise ValueError("policy_version must be nonempty")

    subtaxon = policy.subtaxon_policy
    partial = policy.partial_sequence
    fragment = policy.documentary_fragment

    return {
        "schema_version": SCHEMA_VERSION,
        "stage": "07",
        "policy_version": policy_version,
        "releases": {
            "START": start_release_id,
            "END": end_release_id,
        },
        "purpose": {
            "association": (
                "Determine evidence-supported UniProt candidate "
                "associations with ProBE targets."
            ),
            "donor_eligibility": (
                "Determine which accepted associations may donate "
                "annotations to the downstream GO retrieval stage."
            ),
            "excluded_responsibility": (
                "Stage 07 does not collect GO annotations, construct "
                "ground truth, or select final benchmark targets."
            ),
        },
        "effective_policies": {
            "resolver": {
                "subtaxon_policy": subtaxon,
            },
            "annotation_donor": {
                "partial_sequence": partial,
                "documentary_fragment": fragment,
            },
        },
        "effective_policy_effects": {
            "audited_same_species": {
                "evaluated": subtaxon == "include_audited_same_species",
                "strict_behavior": ("SAME_SPECIES_WRONG_SUBTAXON remains AUDIT_ONLY."),
                "optional_behavior": (
                    "Only existing Stage 05C2 SAME_SPECIES_WRONG_SUBTAXON "
                    "rows with audit_alignment_allowed=1 may enter normal "
                    "resolver evaluation. Direct-anchor provenance must "
                    "be independently verified."
                ),
                "cross_species": (
                    "CROSS_SPECIES remains AUDIT_ONLY under every policy."
                ),
                "discovery_limitation": (
                    "This policy does not broaden Stage 03 or Stage 05R1 "
                    "candidate discovery."
                ),
            },
            "partial_sequence": {
                "value": partial,
                "donation_permitted": partial == "allow",
                "affected_basis": "STABLE_UNIQUE_LOCUS_EXACT_SUBSEQUENCE",
                "report_only_effect": ("Preserve ACCEPTED; set annotation_donor=NO."),
            },
            "documentary_fragment": {
                "value": fragment,
                "donation_permitted": fragment == "allow",
                "report_only_effect": (
                    "Preserve ACCEPTED for an eligible documentary "
                    "fragment; set annotation_donor=NO."
                ),
                "discovery_limitation": (
                    "FRAG=1 similarity-only records remain excluded "
                    "from upstream discovery."
                ),
            },
        },
        "decision_semantics": {
            "ACCEPTED": (
                "An enumerated positive documentary and sequence "
                "evidence rule supports association for enrichment."
            ),
            "UNRESOLVED": (
                "Available evidence is insufficient for automatic "
                "association; biological nonidentity is not asserted."
            ),
            "AUDIT_ONLY": (
                "Candidate is retained for taxonomy audit but is not "
                "accepted for annotation donation."
            ),
            "entity_equivalence": (
                "Association is not necessarily full-record or "
                "full-protein-product equivalence."
            ),
        },
        "evidence_requirements": {
            "taxonomy": (
                "Taxonomy is an explicit eligibility gate rather than "
                "a weighted similarity score."
            ),
            "direct_accession": (
                "Eligible direct PRIMARY or SECONDARY accession "
                "continuity supports acceptance even after sequence "
                "revision. A coincidentally equal accession string "
                "alone does not establish a verified direct anchor."
            ),
            "nondirect_accession": (
                "Nondirect lineage ACCESSION evidence is distinct from "
                "a direct anchor. Exact sequence is required for its "
                "positive acceptance rule."
            ),
            "stable_unique_locus": {
                "key_types": ["ORDERED_LOCUS", "ORF"],
                "conditions": [
                    "One identical typed key supports this candidate pair.",
                    "Independent START and END direct-anchor support exists.",
                    "Stage 04D1 bucket-aware key_collision equals zero.",
                    "The key is not known to be ontology-like or generic.",
                    "The resolver taxonomy gate is satisfied.",
                ],
                "known_nonidentifying_so_pattern": "SO:[0-9]{7}",
                "specificity_note": (
                    "The versioned recognizer detects SO terms after "
                    "percent-decoding for recognition only. Documentary "
                    "joins preserve literal scalar key values. This is "
                    "not a universal ontology registry."
                ),
                "collision_note": (
                    "Eligibility uses authoritative Stage 04D1 "
                    "bucket-aware collision evidence. Global fanout "
                    "thresholds and locus spelling blacklists are "
                    "not used."
                ),
                "nonmatch_note": (
                    "A different or missing locus string does not "
                    "establish biological nonidentity."
                ),
            },
            "exact_candidate_subsequence": [
                "Candidate length is smaller than target length.",
                "Resolved candidate and target TaxIDs are exactly equal.",
                "CANDIDATE_GLOBAL covers the complete candidate.",
                "There are zero mismatches and zero aligned gap residues.",
                "Coordinates prove exact contiguous target containment.",
            ],
            "equal_length_variant": [
                "Resolved candidate and target TaxIDs are exactly equal.",
                "Candidate and target lengths are equal.",
                "TARGET_GLOBAL is complete and ungapped.",
                "At least one substitution is present.",
                "No maximum mismatch threshold is imposed.",
            ],
            "sequence_only": (
                "Exact sequence equality or similarity without "
                "sufficient documentary evidence is not by itself "
                "an automatic association rule."
            ),
        },
        "decision_rule_precedence": [
            {
                "priority": index,
                "condition": condition,
                "decision": decision,
                "decision_basis": basis,
            }
            for index, (condition, decision, basis) in enumerate(RULE_PRECEDENCE)
        ],
        "donor_rules": {
            "necessary_condition": "decision=ACCEPTED",
            "partial_sequence_exception": (
                "With partial_sequence=report_only, an accepted "
                "STABLE_UNIQUE_LOCUS_EXACT_SUBSEQUENCE record cannot donate."
            ),
            "fragment_exception": (
                "With documentary_fragment=report_only, an accepted "
                "documentary FRAG=1 record cannot donate."
            ),
            "combined_exceptions": ("Both donor restrictions apply independently."),
            "not_automatic_exclusions": [
                "entity_equivalence=NOT_ASSERTED",
                "presence of a warning",
                "multiple accepted candidates",
            ],
        },
        "multiplicity": {
            "group_key": ["release", "target_id"],
            "association_key": ["release", "target_id", "rid"],
            "rule": (
                "Preserve all accepted candidates without best-hit "
                "selection, ranking, or automatic collapse."
            ),
            "count_definition": (
                "Count all ACCEPTED rows for the target/release group, "
                "independently of annotation-donor eligibility."
            ),
            "downstream_note": (
                "A future unique-only donor policy belongs to "
                "downstream annotation enrichment, not Stage 07 v1."
            ),
        },
        "provenance_semantics": {
            "scope": ("Immutable origin: PRIMARY or TAXONOMY_AUDIT."),
            "evaluation_scope": (
                "Effective resolver gate; promoted audit-origin rows "
                "may be evaluated as PRIMARY without changing scope."
            ),
            "audit_outcome": (
                "Explains exclusion or optional evaluation of taxonomy-audit rows."
            ),
            "warnings": (
                "Distinct warning tokens are serialized in lexical "
                "order; a dot represents no warnings."
            ),
        },
        "stage08_contract": {
            "association_source": "07_accepted_associations.tsv",
            "selection_criterion": "decision=ACCEPTED in the complete TSV",
            "go_donor_criterion": "annotation_donor=YES",
            "preserve_nondonors": True,
            "preserve_audit_origin": True,
            "must_not_infer": [
                "UNRESOLVED means biological nonidentity",
                "ACCEPTED implies annotation_donor=YES",
                "ACCEPTED implies full-product equivalence",
                "Only one accepted UniProt record exists per target",
                "Similarity alone establishes an association",
            ],
        },
        "references": {
            "complete_decisions": "07_resolved_candidate_associations.tsv",
            "accepted_associations": "07_accepted_associations.tsv",
            "quantitative_summary": "07_summary.json",
            "technical_provenance": "manifest.json",
            "scientific_rules": "docs/STAGE07_RESOLVER_DECISION_RULES.md",
            "technical_specification": (
                "docs/STAGE07_RESOLVER_TECHNICAL_SPEC_REVIEW_V3.md"
            ),
            "interface_contract": ("docs/STAGE07_STAGE08_INTERFACE_CONTRACT.md"),
        },
    }
