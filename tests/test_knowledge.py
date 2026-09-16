from __future__ import annotations

from probe import (
    AnnotationSnapshot,
    EventSuperclass,
    EventType,
    EvidencePolicy,
    GeneOntology,
    IdentityMap,
    OntologyTerm,
    PriorKnowledgeState,
    SequenceAlias,
    SequenceMatch,
    ValidationReport,
    compare_annotations,
)
from probe.records import AnnotationRecord


def annotation(term: str, evidence: str, *, aspect: str = "P") -> AnnotationRecord:
    return AnnotationRecord(
        database="UniProtKB",
        subject_id="P12345",
        symbol="GENE",
        relation={"P": "involved_in", "F": "enables", "C": "located_in"}[aspect],
        term_id=term,
        negated=False,
        references=("PMID:1",),
        evidence=evidence,
        with_from=(),
        aspect=aspect,
        name="",
        synonyms=(),
        object_type="protein",
        taxa=("taxon:9606",),
        date="20230101",
        assigned_by="UniProt",
        extensions=(),
        gene_product_form_id="",
    )


def identities() -> IdentityMap:
    return IdentityMap(
        (
            SequenceMatch(
                "T1",
                "sha256:example",
                (SequenceAlias("P12345", "uniprot"),),
            ),
        ),
        (),
    )


def ontology() -> GeneOntology:
    return GeneOntology(
        [
            OntologyTerm("GO:0008150", namespace="biological_process"),
            OntologyTerm("GO:0003674", namespace="molecular_function"),
            OntologyTerm("GO:0000001", namespace="biological_process"),
            OntologyTerm("GO:0000002", namespace="biological_process"),
            OntologyTerm("GO:0000003", namespace="biological_process"),
            OntologyTerm("GO:0000004", namespace="biological_process"),
            OntologyTerm("GO:0001001", namespace="molecular_function"),
        ],
        [
            ("GO:0000001", "is_a", "GO:0008150"),
            ("GO:0000002", "is_a", "GO:0000001"),
            ("GO:0000003", "part_of", "GO:0000002"),
            ("GO:0000004", "is_a", "GO:0008150"),
            ("GO:0001001", "is_a", "GO:0003674"),
        ],
    )


def compare(
    old_annotations: tuple[AnnotationRecord, ...],
    new_annotations: tuple[AnnotationRecord, ...],
):
    go = ontology()
    return compare_annotations(
        AnnotationSnapshot("old", old_annotations, go, ValidationReport()),
        AnnotationSnapshot("new", new_annotations, go, ValidationReport()),
        identities=identities(),
        evidence_policy=EvidencePolicy.experimental_strict(),
    )


def test_global_and_aspect_none_are_distinct_prior_states():
    global_none = compare((), (annotation("GO:0000004", "IDA"),))
    aspect_none = compare(
        (annotation("GO:0001001", "IDA", aspect="F"),),
        (
            annotation("GO:0001001", "IDA", aspect="F"),
            annotation("GO:0000004", "IDA"),
        ),
    )

    assert (
        global_none.events[0].prior_knowledge_state is PriorKnowledgeState.GLOBAL_NONE
    )
    assert (
        aspect_none.events[0].prior_knowledge_state is PriorKnowledgeState.ASPECT_NONE
    )
    assert global_none.events[0].event_type is EventType.BRANCH_ACQUISITION
    assert global_none.events[0].event_superclass is EventSuperclass.NEW_KNOWLEDGE

    aspect_states = {item.aspect: item.state for item in aspect_none.prior_knowledge}
    assert aspect_states == {
        "F": PriorKnowledgeState.ASPECT_PRESENT,
        "P": PriorKnowledgeState.ASPECT_NONE,
        "C": PriorKnowledgeState.ASPECT_NONE,
    }


def test_nonaccepted_t0_evidence_does_not_create_prior_knowledge():
    result = compare(
        (annotation("GO:0000001", "IEA"),),
        (
            annotation("GO:0000001", "IEA"),
            annotation("GO:0000004", "IDA"),
        ),
    )

    assert result.events[0].prior_knowledge_state is PriorKnowledgeState.GLOBAL_NONE
    assert all(
        item.state is PriorKnowledgeState.GLOBAL_NONE for item in result.prior_knowledge
    )


def test_branch_acquisition_with_prior_aspect_knowledge():
    result = compare(
        (annotation("GO:0000001", "IDA"),),
        (
            annotation("GO:0000001", "IDA"),
            annotation("GO:0000004", "IDA"),
        ),
    )

    event = result.events[0]
    assert event.prior_knowledge_state is PriorKnowledgeState.ASPECT_PRESENT
    assert event.event_type is EventType.BRANCH_ACQUISITION
    assert event.event_superclass is EventSuperclass.NEW_KNOWLEDGE
    assert event.qualifies_for_benchmark


def test_descendant_is_specificity_refinement():
    result = compare(
        (annotation("GO:0000001", "IDA"),),
        (
            annotation("GO:0000001", "IDA"),
            annotation("GO:0000002", "IDA"),
        ),
    )

    event = result.events[0]
    assert event.event_type is EventType.SPECIFICITY_REFINEMENT
    assert event.event_superclass is EventSuperclass.KNOWLEDGE_REFINEMENT
    assert event.qualifies_for_benchmark


def test_redundant_ancestor_does_not_qualify():
    result = compare(
        (annotation("GO:0000002", "IDA"),),
        (
            annotation("GO:0000002", "IDA"),
            annotation("GO:0000001", "IDA"),
        ),
    )

    event = result.events[0]
    assert event.event_type is EventType.REDUNDANT_ANCESTOR
    assert event.event_superclass is EventSuperclass.REDUNDANT_OR_NON_EVALUABLE
    assert not event.qualifies_for_benchmark
    assert result.selected_targets == set()


def test_redundant_ancestor_precedes_refinement_in_ambiguous_dag_case():
    result = compare(
        (
            annotation("GO:0000001", "IDA"),
            annotation("GO:0000003", "IDA"),
        ),
        (
            annotation("GO:0000001", "IDA"),
            annotation("GO:0000002", "IDA"),
            annotation("GO:0000003", "IDA"),
        ),
    )

    assert result.events[0].event_type is EventType.REDUNDANT_ANCESTOR
    assert not result.events[0].qualifies_for_benchmark


def test_evidence_upgrade_is_a_separate_superclass():
    result = compare(
        (annotation("GO:0000001", "IEA"),),
        (annotation("GO:0000001", "IDA"),),
    )

    event = result.events[0]
    assert event.event_type is EventType.EVIDENCE_UPGRADE
    assert event.event_superclass is EventSuperclass.EVIDENCE_CONFIRMATION
    assert event.old_evidence == {"IEA"}
    assert event.new_evidence == {"IDA"}
