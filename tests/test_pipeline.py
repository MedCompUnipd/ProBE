from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from probe.evidence import EvidencePolicy
from probe.masking import (
    EvaluationUniversePolicy,
    TargetAspectStatus,
    construct_truth_and_masks,
)
from probe.pipeline import (
    GroundTruthNotFinalError,
    OntologyInput,
    ReleaseInput,
    TruthMaskConfiguration,
    run_benchmark_pipeline,
)
from probe.truth import (
    ConfirmationMaskPolicy,
    NeutralTruthPolicy,
    PositiveAssertionPolicy,
    TruthSelectionProfile,
)


def _write_ontology(path) -> None:
    path.write_text(
        """<?xml version="1.0"?>
<rdf:RDF
  xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
  xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"
  xmlns:owl="http://www.w3.org/2002/07/owl#"
  xmlns:oboInOwl="http://www.geneontology.org/formats/oboInOwl#">
  <owl:Ontology rdf:about="http://purl.obolibrary.org/obo/go.owl">
    <owl:versionIRI rdf:resource="http://purl.obolibrary.org/obo/go/releases/2026-01-01/go.owl"/>
  </owl:Ontology>
  <owl:Class rdf:about="http://purl.obolibrary.org/obo/GO_0008150">
    <oboInOwl:hasOBONamespace>biological_process</oboInOwl:hasOBONamespace>
  </owl:Class>
  <owl:Class rdf:about="http://purl.obolibrary.org/obo/GO_0003674">
    <oboInOwl:hasOBONamespace>molecular_function</oboInOwl:hasOBONamespace>
  </owl:Class>
  <owl:Class rdf:about="http://purl.obolibrary.org/obo/GO_0005575">
    <oboInOwl:hasOBONamespace>cellular_component</oboInOwl:hasOBONamespace>
  </owl:Class>
  <owl:Class rdf:about="http://purl.obolibrary.org/obo/GO_0000001">
    <oboInOwl:hasOBONamespace>biological_process</oboInOwl:hasOBONamespace>
    <rdfs:subClassOf rdf:resource="http://purl.obolibrary.org/obo/GO_0008150"/>
  </owl:Class>
  <owl:Class rdf:about="http://purl.obolibrary.org/obo/GO_0000002">
    <oboInOwl:hasOBONamespace>biological_process</oboInOwl:hasOBONamespace>
    <rdfs:subClassOf rdf:resource="http://purl.obolibrary.org/obo/GO_0008150"/>
  </owl:Class>
</rdf:RDF>
""",
        encoding="utf-8",
    )


def _gaf_row(accession: str, term_id: str, evidence: str) -> str:
    return "\t".join(
        [
            "UniProtKB",
            accession,
            accession,
            "involved_in",
            term_id,
            "PMID:1",
            evidence,
            "",
            "P",
            accession,
            "",
            "protein",
            "taxon:9606",
            "20260101",
            "UniProt",
            "",
            "",
        ]
    )


def test_pipeline_matches_each_release_and_preserves_direct_evidence(tmp_path):
    ontology = tmp_path / "go.owl"
    targets = tmp_path / "targets.fasta"
    fasta_t0 = tmp_path / "uniprot_t0.fasta"
    fasta_t1 = tmp_path / "uniprot_t1.fasta"
    goa_t0 = tmp_path / "goa_t0.gaf"
    goa_t1 = tmp_path / "goa_t1.gaf"
    _write_ontology(ontology)
    targets.write_text(">TARGET_A\nAAAA\n>TARGET_UNMATCHED\nCCCC\n", encoding="utf-8")
    fasta_t0.write_text(">sp|OLD_A|OLD_A\nAAAA\n", encoding="utf-8")
    fasta_t1.write_text(">sp|NEW_A|NEW_A\nAAAA\n", encoding="utf-8")
    goa_t0.write_text(
        f"!gaf-version: 2.2\n{_gaf_row('OLD_A', 'GO:0000001', 'IEA')}\n",
        encoding="utf-8",
    )
    goa_t1.write_text(
        "!gaf-version: 2.2\n"
        f"{_gaf_row('NEW_A', 'GO:0000001', 'IDA')}\n"
        f"{_gaf_row('NEW_A', 'GO:0000002', 'IMP')}\n",
        encoding="utf-8",
    )
    ontology_hash = hashlib.sha256(ontology.read_bytes()).hexdigest()

    evidence_policy = EvidencePolicy.experimental_strict()
    truth_mask = TruthMaskConfiguration(
        truth_profile=TruthSelectionProfile.combined(evidence_policy),
        prior_policy=PositiveAssertionPolicy.all_usable_positive_t0(),
        neutral_policy=NeutralTruthPolicy.primary_non_electronic_neutral(),
        confirmation_mask_policy=ConfirmationMaskPolicy.main_knowledge_gain(),
        universe_policy=EvaluationUniversePolicy.is_a_rooted(),
    )
    result = run_benchmark_pipeline(
        targets_fasta=targets,
        ontology=OntologyInput(
            path=ontology,
            expected_sha256=ontology_hash,
            source_url="http://purl.obolibrary.org/obo/go.owl",
            retrieval_date="2026-09-20",
        ),
        t0=ReleaseInput("t0", fasta_t0, goa_t0),
        t1=ReleaseInput("t1", fasta_t1, goa_t1),
        evidence_policy=evidence_policy,
        truth_mask=truth_mask,
    )

    assert result.t0.identities.matches[0].target_id == "TARGET_A"
    assert result.t0.identities.matches[0].aliases[0].identifier == "OLD_A"
    assert result.t1.identities.matches[0].aliases[0].identifier == "NEW_A"
    assert result.identities.aliases_for(
        result.t0.identities.matches[0].sequence_id
    ) == (
        result.t1.identities.matches[0].aliases[0],
        result.t0.identities.matches[0].aliases[0],
    )
    assert result.identities.unmatched_targets == ("TARGET_UNMATCHED",)
    assert {
        (candidate.term_id, candidate.event_type.value)
        for candidate in result.direct_candidates
    } == {
        ("GO:0000001", "evidence_upgrade"),
        ("GO:0000002", "branch_acquisition"),
    }
    evidence_by_term = {
        candidate.term_id: (candidate.old_evidence, candidate.new_evidence)
        for candidate in result.direct_candidates
    }
    assert evidence_by_term == {
        "GO:0000001": (frozenset({"IEA"}), frozenset({"IDA"})),
        "GO:0000002": (frozenset(), frozenset({"IMP"})),
    }
    assert result.evidence_policy.name == "experimental_strict"
    assert result.preflight.is_valid
    assert result.final_ground_truth is not None
    bp_truth = next(
        item for item in result.require_final_ground_truth() if item.aspect.value == "P"
    )
    assert bp_truth.d_score.terms == {"GO:0000002"}
    assert bp_truth.g_candidate.terms == {"GO:0000002", "GO:0008150"}
    assert bp_truth.k0.direct_terms == {"GO:0000001"}
    assert bp_truth.d_neutral1.terms == {"GO:0000001"}
    assert bp_truth.x1.raw_closure == {"GO:0000001", "GO:0008150"}
    assert bp_truth.x1.terms == {"GO:0000001"}
    universe = result.evaluation_universes[bp_truth.aspect]
    assert bp_truth.m == universe.terms - bp_truth.k0.terms - bp_truth.x1.terms
    assert bp_truth.truth == {"GO:0000002"}
    assert bp_truth.truth == bp_truth.g_candidate.terms & bp_truth.m
    assert bp_truth.status is TargetAspectStatus.EVALUABLE
    assert result.direct_candidates[0] not in result.require_final_ground_truth()
    assert result.comparison.ontology_fingerprint == result.ontology.fingerprint.value
    with pytest.raises(ValueError, match="comparison/ontology fingerprint mismatch"):
        construct_truth_and_masks(
            replace(result.comparison, ontology_fingerprint="different"),
            ontology=result.ontology,
            truth_profile=truth_mask.truth_profile,
            prior_policy=truth_mask.prior_policy,
            neutral_policy=truth_mask.neutral_policy,
            confirmation_mask_policy=truth_mask.confirmation_mask_policy,
            approved_preflight=result.preflight,
            universes=result.evaluation_universes,
        )

    confirmation = run_benchmark_pipeline(
        targets_fasta=targets,
        ontology=OntologyInput(
            path=ontology,
            expected_sha256=ontology_hash,
            source_url="http://purl.obolibrary.org/obo/go.owl",
            retrieval_date="2026-09-20",
        ),
        t0=ReleaseInput("t0", fasta_t0, goa_t0),
        t1=ReleaseInput("t1", fasta_t1, goa_t1),
        evidence_policy=evidence_policy,
        truth_mask=TruthMaskConfiguration(
            truth_profile=TruthSelectionProfile.evidence_confirmation(evidence_policy),
            prior_policy=PositiveAssertionPolicy.all_usable_positive_t0(),
            neutral_policy=NeutralTruthPolicy.primary_non_electronic_neutral(),
            confirmation_mask_policy=(
                ConfirmationMaskPolicy.unmask_upgraded_direct_only()
            ),
            universe_policy=EvaluationUniversePolicy.is_a_rooted(),
        ),
    )
    confirmation_bp = next(
        item
        for item in confirmation.require_final_ground_truth()
        if item.aspect.value == "P"
    )
    assert confirmation_bp.d_score.terms == {"GO:0000001"}
    assert "GO:0000001" not in confirmation_bp.k0.terms
    assert "GO:0008150" in confirmation_bp.k0.terms
    assert confirmation_bp.truth == {"GO:0000001"}


def test_pipeline_treats_a_sequence_change_under_one_accession_as_release_specific(
    tmp_path,
):
    ontology = tmp_path / "go.owl"
    targets = tmp_path / "targets.fasta"
    fasta_t0 = tmp_path / "uniprot_t0.fasta"
    fasta_t1 = tmp_path / "uniprot_t1.fasta"
    goa_t0 = tmp_path / "goa_t0.gaf"
    goa_t1 = tmp_path / "goa_t1.gaf"
    _write_ontology(ontology)
    targets.write_text(">TARGET_A\nAAAA\n", encoding="utf-8")
    fasta_t0.write_text(">sp|P_SHARED|OLD\nCCCC\n", encoding="utf-8")
    fasta_t1.write_text(">sp|P_SHARED|NEW\nAAAA\n", encoding="utf-8")
    goa_t0.write_text(
        f"!gaf-version: 2.2\n{_gaf_row('P_SHARED', 'GO:0000001', 'IDA')}\n",
        encoding="utf-8",
    )
    goa_t1.write_text(
        f"!gaf-version: 2.2\n{_gaf_row('P_SHARED', 'GO:0000002', 'IDA')}\n",
        encoding="utf-8",
    )

    result = run_benchmark_pipeline(
        targets_fasta=targets,
        ontology=OntologyInput(
            path=ontology,
            expected_sha256=hashlib.sha256(ontology.read_bytes()).hexdigest(),
            source_url="http://purl.obolibrary.org/obo/go.owl",
            retrieval_date="2026-09-20",
        ),
        t0=ReleaseInput("t0", fasta_t0, goa_t0),
        t1=ReleaseInput("t1", fasta_t1, goa_t1),
        evidence_policy=EvidencePolicy.experimental_strict(),
    )

    assert result.t0.identities.matches == ()
    assert result.t1.identities.matches[0].aliases[0].identifier == "P_SHARED"
    assert [candidate.term_id for candidate in result.direct_candidates] == [
        "GO:0000002"
    ]
    assert result.final_ground_truth is None
    with pytest.raises(GroundTruthNotFinalError, match="Q, K0, X1, M, and T"):
        result.require_final_ground_truth()
