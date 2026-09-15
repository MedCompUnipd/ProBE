from __future__ import annotations

import pytest

from probe import (
    AnnotationSnapshot,
    ChangeKind,
    EvidencePolicy,
    ExclusionReason,
    GeneOntology,
    IdentityMap,
    OntologyTerm,
    SequenceAlias,
    SequenceMatch,
    ValidationError,
    ValidationReport,
    compare_annotations,
)
from probe.records import AnnotationRecord


def annotation(
    term: str,
    evidence: str,
    *,
    negated: bool = False,
) -> AnnotationRecord:
    return AnnotationRecord(
        database="UniProtKB",
        subject_id="P12345",
        symbol="GENE",
        relation="involved_in",
        term_id=term,
        negated=negated,
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
            OntologyTerm("GO:0000001", namespace="biological_process"),
            OntologyTerm("GO:0000002", namespace="biological_process"),
            OntologyTerm("GO:0000003", namespace="biological_process"),
            OntologyTerm(
                "GO:0000004",
                label="obsolete renamed process",
                namespace="biological_process",
                obsolete=True,
                deprecated=True,
                replaced_by=("GO:0000002",),
            ),
        ],
        [
            ("GO:0000001", "is_a", "GO:0008150"),
            ("GO:0000002", "is_a", "GO:0000001"),
            ("GO:0000003", "is_a", "GO:0000002"),
        ],
    )


def test_comparison_detects_acquisition_and_upgrade_with_one_ontology():
    go = ontology()
    old = AnnotationSnapshot(
        "old",
        (annotation("GO:0000001", "IEA"),),
        go,
        ValidationReport(),
    )
    new = AnnotationSnapshot(
        "new",
        (
            annotation("GO:0000001", "IDA"),
            annotation("GO:0000003", "IDA"),
        ),
        go,
        ValidationReport(),
    )

    result = compare_annotations(
        old,
        new,
        identities=identities(),
        evidence_policy=EvidencePolicy.experimental(),
    )

    assert [change.kind for change in result.changes] == [
        ChangeKind.EVIDENCE_UPGRADED,
        ChangeKind.TERM_ACQUIRED,
    ]
    assert result.selected_targets == {"T1"}
    assert result.exclusions == ()


def test_obsolete_id_with_one_replacement_is_compared_as_canonical_id():
    go = ontology()
    old = AnnotationSnapshot(
        "old",
        (annotation("GO:0000002", "IDA"),),
        go,
        ValidationReport(),
    )
    new = AnnotationSnapshot(
        "new",
        (annotation("GO:0000004", "IDA"),),
        go,
        ValidationReport(),
    )

    result = compare_annotations(old, new, identities=identities())

    assert result.changes == ()


def test_not_assertion_removes_its_node_and_descendant_positive_assertions():
    go = ontology()
    old = AnnotationSnapshot("old", (), go, ValidationReport())
    new = AnnotationSnapshot(
        "new",
        (
            annotation("GO:0000002", "IDA", negated=True),
            annotation("GO:0000003", "IDA"),
            annotation("GO:0000001", "IDA"),
        ),
        go,
        ValidationReport(),
    )

    result = compare_annotations(old, new, identities=identities())

    assert [change.term_id for change in result.changes] == ["GO:0000001"]
    assert {exclusion.reason for exclusion in result.exclusions} == {
        ExclusionReason.NOT_ASSERTION,
        ExclusionReason.TRUE_PATH_VIOLATION,
    }


def test_comparison_requires_the_same_ontology_object():
    old = AnnotationSnapshot("old", (), ontology(), ValidationReport())
    new = AnnotationSnapshot("new", (), ontology(), ValidationReport())

    with pytest.raises(ValidationError) as caught:
        compare_annotations(old, new, identities=identities())

    assert caught.value.issues[0].code == "ONTOLOGY_SNAPSHOT_MISMATCH"


def test_experimental_evidence_is_not_upgraded_twice():
    policy = EvidencePolicy.experimental()

    assert policy.is_upgrade(frozenset({"IEA"}), frozenset({"IDA"}))
    assert not policy.is_upgrade(frozenset({"IEA", "IMP"}), frozenset({"IDA"}))
