"""Stage B1 input and output contracts for lossless candidate provenance.

This ledger is not Stage 05A2, an alignment job, or a GO donor decision.
It deliberately keeps the raw records from independent source channels.
"""

from __future__ import annotations

from probe.stage2c_candidate_union import FULL_REQUIRED, GO_REQUIRED
from probe.stage2c_evidence_bridge import GO_FACTS, GO_SEQ

SCHEMA = "probe-canonical-v2-candidate-ledger-v1"
OUTPUT_NAME = "candidate_provenance.tsv"
SUMMARY_NAME = "candidate_provenance.summary.json"

DISCOVERY_REQUIRED = frozenset(
    {
        "release",
        "target_id",
        "rid",
        "candidate_accession",
        "candidate_raw_ox",
        "target_resolved_taxid",
        "candidate_resolved_taxid",
        "species_taxid",
        "go_positive_species_wide",
        "full_exact_species_wide",
        "go_positive_exact",
        "go_positive_record_number",
        "full_record_number",
        "strict_gate",
        "same_species_exact_gate",
    }
)
GO_SOURCE_REQUIRED = GO_REQUIRED | frozenset(GO_FACTS) | frozenset(GO_SEQ)
FULL_SOURCE_REQUIRED = FULL_REQUIRED

LINEAGE_EVIDENCE_FIELDS = frozenset(
    {
        "lineage_evidence_row_count",
        "distinct_lineage_key_count",
        "lineage_fact_signature",
        "record_match_roles",
        "source_roles",
        "target_accession_match",
        "from_start_anchor",
        "from_end_anchor",
        "has_accession_primary_match",
        "has_accession_secondary_match",
        "has_ordered_locus_match",
        "has_orf_match",
        "key_collision_present",
        "collision_key_count",
        "max_key_target_multiplicity",
        "matched_typed_keys",
        "target_accession_keys",
        "accession_primary_keys",
        "accession_secondary_keys",
        "ordered_locus_keys",
        "orf_keys",
        "collision_keys",
    }
)
LINEAGE_REQUIRED = LINEAGE_EVIDENCE_FIELDS | frozenset(
    {
        "release",
        "target_id",
        "rid",
        "record_number",
        "candidate_primary_accession",
        "entry",
        "raw_ox",
        "resolved_taxid",
        "species_taxid",
        "frag",
        "sequence_length",
        "pe",
        "sv",
        "sec",
        "gene",
        "target_primary_bucket",
    }
)
RESCUE_REQUIRED = GO_SOURCE_REQUIRED

OUTPUT_FIELDS = (
    "release",
    "target_id",
    "rid",
    "candidate_accession",
    "candidate_raw_ox",
    "candidate_resolved_taxid",
    "species_taxid",
    "frag",
    "go_positive_species_wide",
    "full_exact_species_wide",
    "lineage_discovered",
    "full_v2_rescue_discovered",
    "go_positive_exact",
    "go_positive_record_number",
    "full_record_number",
    "strict_gate",
    "same_species_exact_gate",
    "go_source_json",
    "full_source_json",
    "lineage_source_json",
    "rescue_source_json",
)
