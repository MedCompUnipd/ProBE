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
    relation: str = "involved_in",
    references: tuple[str, ...] = ("PMID:1",),
    extensions: tuple[str, ...] = (),
    gene_product_form_id: str = "",
    assigned_by: str = "UniProt",
    date: str = "20230101",
) -> AnnotationRecord:
    return AnnotationRecord(
        database="UniProtKB",
        subject_id="P12345",
        symbol="GENE",
        relation=relation,
        term_id=term,
        negated=negated,
        references=references,
        evidence=evidence,
        with_from=(),
        aspect="P",
        name="",
        synonyms=(),
        object_type="protein",
        taxa=("taxon:9606",),
        date=date,
        assigned_by=assigned_by,
        extensions=extensions,
        gene_product_form_id=gene_product_form_id,
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
            OntologyTerm(
                "GO:0000001",
                namespace="biological_process",
                alternate_ids=("GO:9999999",),
            ),
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


def test_relation_only_change_is_not_removal_or_acquisition():
    go = ontology()
    old = AnnotationSnapshot(
        "old",
        (annotation("GO:0000001", "IDA", relation="involved_in"),),
        go,
        ValidationReport(),
    )
    new = AnnotationSnapshot(
        "new",
        (annotation("GO:0000001", "IDA", relation="acts_upstream_of"),),
        go,
        ValidationReport(),
    )

    result = compare_annotations(
        old,
        new,
        identities=identities(),
        evidence_policy=EvidencePolicy.experimental_strict(),
    )

    assert result.changes == ()
    assert result.events == ()
    assert result.selected_targets == set()
    assert {context.relation for context in result.old_assertions[0].contexts} == {
        "involved_in"
    }
    assert {context.relation for context in result.new_assertions[0].contexts} == {
        "acts_upstream_of"
    }


def test_other_context_only_changes_do_not_create_biological_events():
    go = ontology()
    old = AnnotationSnapshot(
        "old",
        (
            annotation(
                "GO:0000001",
                "IDA",
                references=("PMID:1",),
                assigned_by="UniProt",
                date="20230101",
            ),
        ),
        go,
        ValidationReport(),
    )
    new = AnnotationSnapshot(
        "new",
        (
            annotation(
                "GO:0000001",
                "IDA",
                references=("PMID:2",),
                extensions=("occurs_in(CL:1)",),
                gene_product_form_id="UniProtKB:P12345-2",
                assigned_by="GO_Central",
                date="20250101",
            ),
        ),
        go,
        ValidationReport(),
    )

    result = compare_annotations(
        old,
        new,
        identities=identities(),
        evidence_policy=EvidencePolicy.experimental_strict(),
    )

    assert result.changes == ()
    assert result.events == ()
    assert result.selected_targets == set()
    assert result.old_assertions[0].contexts != result.new_assertions[0].contexts


def test_canonical_aggregation_preserves_all_evidence_and_contexts_once():
    go = ontology()
    old = AnnotationSnapshot("old", (), go, ValidationReport())
    rows = (
        annotation("GO:9999999", "IEA", references=("PMID:1",)),
        annotation(
            "GO:0000001",
            "IMP",
            references=("PMID:2",),
            extensions=("occurs_in(CL:1)",),
        ),
        annotation(
            "GO:0000001",
            "IMP",
            references=("PMID:2",),
            extensions=("occurs_in(CL:1)",),
        ),
    )
    new = AnnotationSnapshot("new", rows, go, ValidationReport())

    result = compare_annotations(
        old,
        new,
        identities=identities(),
        evidence_policy=EvidencePolicy.experimental_strict(),
    )

    assert len(result.changes) == 1
    assert len(result.events) == 1
    state = result.new_assertions[0]
    assert state.term_id == "GO:0000001"
    assert state.evidence_codes == {"IEA", "IMP"}
    assert state.normalized_from == {"GO:9999999"}
    assert len(state.contexts) == 2


def test_nonaccepted_and_negated_assertions_do_not_select_targets():
    go = ontology()
    old = AnnotationSnapshot("old", (), go, ValidationReport())
    new = AnnotationSnapshot(
        "new",
        (
            annotation("GO:0000001", "IEA"),
            annotation("GO:0000002", "NAS"),
            annotation("GO:0000003", "ND"),
            annotation("GO:0000002", "IDA", negated=True),
        ),
        go,
        ValidationReport(),
    )

    result = compare_annotations(
        old,
        new,
        identities=identities(),
        evidence_policy=EvidencePolicy.experimental_strict(),
    )

    assert result.events == ()
    assert result.selected_targets == set()


def test_changes_inside_an_accepted_profile_are_not_upgrades():
    go = ontology()
    old = AnnotationSnapshot(
        "old", (annotation("GO:0000001", "EXP"),), go, ValidationReport()
    )
    new = AnnotationSnapshot(
        "new", (annotation("GO:0000001", "IDA"),), go, ValidationReport()
    )

    result = compare_annotations(
        old,
        new,
        identities=identities(),
        evidence_policy=EvidencePolicy.experimental_strict(),
    )

    assert result.events == ()
    assert result.changes[0].kind is ChangeKind.EVIDENCE_CHANGED
    assert not result.changes[0].qualifies


def test_aggregated_experimental_evidence_prevents_a_later_upgrade():
    go = ontology()
    old = AnnotationSnapshot(
        "old",
        (
            annotation("GO:0000001", "IEA"),
            annotation("GO:0000001", "IMP"),
        ),
        go,
        ValidationReport(),
    )
    new = AnnotationSnapshot(
        "new", (annotation("GO:0000001", "IDA"),), go, ValidationReport()
    )

    result = compare_annotations(
        old,
        new,
        identities=identities(),
        evidence_policy=EvidencePolicy.experimental_strict(),
    )

    assert result.events == ()
    assert result.selected_targets == set()


def test_comparison_is_invariant_to_input_order():
    go = ontology()
    rows = (
        annotation("GO:0000002", "IDA"),
        annotation("GO:0000001", "IEA"),
        annotation("GO:0000001", "IMP"),
    )

    forward = compare_annotations(
        AnnotationSnapshot("old", (), go, ValidationReport()),
        AnnotationSnapshot("new", rows, go, ValidationReport()),
        identities=identities(),
        evidence_policy=EvidencePolicy.experimental_strict(),
    )
    reverse = compare_annotations(
        AnnotationSnapshot("old", (), go, ValidationReport()),
        AnnotationSnapshot("new", tuple(reversed(rows)), go, ValidationReport()),
        identities=identities(),
        evidence_policy=EvidencePolicy.experimental_strict(),
    )

    assert forward.changes == reverse.changes
    assert forward.events == reverse.events
    assert forward.new_assertions == reverse.new_assertions
