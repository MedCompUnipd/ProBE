from __future__ import annotations

from probe import (
    AnnotationSnapshot,
    ChangeKind,
    EvidencePolicy,
    GeneOntology,
    IdentityMap,
    OntologyTerm,
    SequenceAlias,
    SequenceMatch,
    ValidationReport,
    compare_annotations,
)
from probe.records import AnnotationRecord


def annotation(term: str, evidence: str) -> AnnotationRecord:
    return AnnotationRecord(
        database="UniProtKB",
        subject_id="P12345",
        symbol="GENE",
        relation="involved_in",
        term_id=term,
        negated=False,
        references=("PMID:1",),
        evidence=evidence,
        with_from=(),
        aspect="P",
        name="",
        synonyms=(),
        object_type="protein",
        taxa=("taxon:9606",),
        date="20230101",
        assigned_by="UniProt",
        extensions=(),
        gene_product_form_id="",
    )


def test_comparison_detects_acquisition_upgrade_and_projection():
    old_ontology = GeneOntology(
        [
            OntologyTerm("GO:0008150", namespace="biological_process"),
            OntologyTerm("GO:0000001", namespace="biological_process"),
            OntologyTerm("GO:0000002", namespace="biological_process"),
        ],
        [
            ("GO:0000001", "is_a", "GO:0008150"),
            ("GO:0000002", "is_a", "GO:0008150"),
        ],
    )
    new_ontology = GeneOntology(
        [
            *old_ontology.terms.values(),
            OntologyTerm("GO:0000003", namespace="biological_process"),
        ],
        [
            ("GO:0000001", "is_a", "GO:0008150"),
            ("GO:0000002", "is_a", "GO:0008150"),
            ("GO:0000003", "is_a", "GO:0000002"),
        ],
    )
    old = AnnotationSnapshot(
        "old",
        (annotation("GO:0000001", "IEA"),),
        old_ontology,
        ValidationReport(),
    )
    new = AnnotationSnapshot(
        "new",
        (
            annotation("GO:0000001", "IDA"),
            annotation("GO:0000003", "IDA"),
        ),
        new_ontology,
        ValidationReport(),
    )
    identities = IdentityMap(
        (
            SequenceMatch(
                "T1",
                "sha256:example",
                (SequenceAlias("P12345", "uniprot"),),
            ),
        ),
        (),
    )

    result = compare_annotations(
        old,
        new,
        identities=identities,
        evidence_policy=EvidencePolicy.experimental(),
    )

    assert [change.kind for change in result.changes] == [
        ChangeKind.EVIDENCE_UPGRADED,
        ChangeKind.TERM_ACQUIRED,
    ]
    assert result.changes[1].projected_from == {"GO:0000003"}
    assert result.selected_targets == {"T1"}


def test_experimental_evidence_is_not_upgraded_twice():
    policy = EvidencePolicy.experimental()

    assert policy.is_upgrade(frozenset({"IEA"}), frozenset({"IDA"}))
    assert not policy.is_upgrade(frozenset({"IEA", "IMP"}), frozenset({"IDA"}))
