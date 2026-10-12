"""M10b literal donor/source provenance and GO propagation tests."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from probe.stage08_go_closure import GoClosureIndex
from probe.stage08_go_supports import (
    propagate_projected_assertions,
    summarize_sorted_term_supports,
)


@dataclass(frozen=True)
class FakeDonor:
    release: str
    target_id: str
    rid: str
    candidate_accession: str
    may_donate: bool = True

    @property
    def key(self):
        return (self.release, self.target_id, self.rid)


@pytest.fixture
def graph():
    return GoClosureIndex(
        {"F1": "F", "F2": "F", "F3": "F", "F4": "F", "P1": "P"},
        [
            ("F2", "is_a", "F1"),
            ("F3", "part_of", "F2"),
            ("F4", "is_a", "F1"),
        ],
    )


def source(
    *,
    direct="F3",
    aspect="F",
    negated=False,
    evidence="IDA",
    qualifier="enables",
    accession="P11111",
    release="2023_01",
    line=19,
    donor_keys=None,
):
    if donor_keys is None:
        donor_keys = [("TARGET1", "7", accession)]
    annotation = SimpleNamespace(
        term_id=direct,
        aspect=aspect,
        negated=negated,
        evidence=evidence,
        relation=qualifier.replace("NOT|", ""),
        subject_id=accession,
        database="UniProtKB",
    )
    assertion = SimpleNamespace(
        annotation=annotation,
        source=SimpleNamespace(release=release, sha256="a" * 64),
        assertion_id=f"sha:{release}:{line}",
        line_number=line,
        raw_fields=(".", ".", ".", qualifier) + (".",) * 13,
    )
    donors = tuple(
        FakeDonor(release, target, rid, candidate)
        for target, rid, candidate in donor_keys
    )
    return SimpleNamespace(assertion=assertion, donors=donors)


def emitted(graph, *items):
    return list(propagate_projected_assertions(items, graph=graph))


def test_positive_closure_has_direct_and_inherited(graph):
    rows = emitted(graph, source())
    assert [(r.go_id, r.origin) for r in rows] == [
        ("F1", "INHERITED"),
        ("F2", "INHERITED"),
        ("F3", "DIRECT"),
    ]


def test_not_closure_to_descendants_preserves_not(graph):
    rows = emitted(
        graph,
        source(direct="F1", negated=True, qualifier="NOT|enables"),
    )
    assert {r.go_id for r in rows} == {"F1", "F2", "F3", "F4"}
    assert {r.polarity for r in rows} == {"NOT"}
    assert {r.gaf_qualifier for r in rows} == {"NOT|enables"}


def test_positive_keeps_literal_evidence_and_relation(graph):
    rows = emitted(graph, source(evidence="X_NEW", qualifier="contributes_to"))
    assert {r.evidence_code for r in rows} == {"X_NEW"}
    assert {r.gaf_relation for r in rows} == {"contributes_to"}
    assert {r.gaf_qualifier for r in rows} == {"contributes_to"}


def test_donor_multiplicity_retained(graph):
    item = source(
        donor_keys=[("TARGET1", "7", "P11111"), ("TARGET2", "9", "P11111")],
    )
    rows = emitted(graph, item)
    assert len(rows) == 6
    assert {r.source_link_key for r in rows} == {
        ("2023_01", "TARGET1", "7", "sha:2023_01:19"),
        ("2023_01", "TARGET2", "9", "sha:2023_01:19"),
    }


def test_identical_go_from_multiple_donors_retains_supports(graph):
    a = source(line=19, evidence="IDA")
    b = source(line=28, evidence="IEA")
    rows = emitted(graph, a, b)
    subset = [r for r in rows if r.go_id == "F1"]
    assert len(subset) == 2
    assert {r.evidence_code for r in subset} == {"IDA", "IEA"}
    assert len({r.assertion_id for r in subset}) == 2


def test_negative_and_positive_same_go_neither_vetoes(graph):
    rows = emitted(
        graph,
        source(direct="F3", line=19),
        source(direct="F1", negated=True, qualifier="NOT|enables", line=27),
    )
    summary = list(
        summarize_sorted_term_supports(sorted(rows, key=lambda item: item.term_key))
    )
    assert {s.go_id for s in summary if s.is_conflict} == {"F1", "F2", "F3"}
    assert summary[-1].go_id == "F4"
    assert summary[-1].has_not and not summary[-1].has_positive
    assert {r.polarity for r in rows} == {"POSITIVE", "NOT"}


def test_direct_not_and_inherited_positive_conflict(graph):
    rows = emitted(
        graph,
        source(direct="F1", negated=True, qualifier="NOT|enables"),
        source(direct="F3", line=32),
    )
    per_term = {
        row.go_id: row
        for row in summarize_sorted_term_supports(
            sorted(rows, key=lambda item: item.term_key)
        )
    }
    assert per_term["F3"].is_conflict
    assert per_term["F4"].has_not and not per_term["F4"].has_positive


def test_release_separated(graph):
    rows = emitted(
        graph,
        source(release="2023_01"),
        source(release="2026_03", line=19),
    )
    assert {r.release for r in rows} == {"2023_01", "2026_03"}
    assert (
        len(
            list(
                summarize_sorted_term_supports(
                    sorted(rows, key=lambda row: row.term_key)
                )
            )
        )
        == 6
    )


def test_provenance_of_inherited_support(graph):
    result = emitted(graph, source())[0]
    assert result.source_go_id == "F3"
    assert result.go_id == "F1"
    assert result.donor_accession == "P11111"
    assert result.rid == "7"
    assert result.normalized_gaf_line == 19
    assert result.normalized_gaf_sha256 == "a" * 64
    assert result.source_database == "UniProtKB"
    assert graph.witness_path("F3", "F1", direction="positive") == ("F3", "F2", "F1")


def test_unknown_go_fails_closed(graph):
    with pytest.raises(ValueError, match="Unknown GO"):
        emitted(graph, source(direct="GO:9999999"))


def test_aspect_mismatch_fails_closed(graph):
    with pytest.raises(ValueError, match="disagrees"):
        emitted(graph, source(aspect="P"))


def test_missing_authorized_donors_fails_closed(graph):
    with pytest.raises(ValueError, match="donor links"):
        emitted(graph, SimpleNamespace(assertion=source().assertion, donors=()))


def test_donor_disabled_fails_closed(graph):
    item = source()
    bad = FakeDonor("2023_01", "TARGET1", "7", "P11111", False)
    with pytest.raises(ValueError, match="not authorized"):
        emitted(graph, SimpleNamespace(assertion=item.assertion, donors=(bad,)))


@pytest.mark.parametrize("replacement", ["B45678", "", "P22222"])
def test_wrong_donor_accession_fails_closed(graph, replacement):
    item = source()
    bad = FakeDonor("2023_01", "TARGET1", "7", replacement)
    with pytest.raises(ValueError, match="not authorized"):
        emitted(graph, SimpleNamespace(assertion=item.assertion, donors=(bad,)))


def test_wrong_donor_release_fails_closed(graph):
    item = source()
    bad = FakeDonor("2026_03", "TARGET1", "7", "P11111")
    with pytest.raises(ValueError, match="not authorized"):
        emitted(graph, SimpleNamespace(assertion=item.assertion, donors=(bad,)))


def test_duplicate_donor_link_fails_closed(graph):
    item = source()
    duplicate = (item.donors[0], item.donors[0])
    with pytest.raises(ValueError, match="Duplicate donor"):
        emitted(graph, SimpleNamespace(assertion=item.assertion, donors=duplicate))


def test_summary_unsorted_fails_closed(graph):
    items = [
        source(direct="F3", line=19),
        source(direct="F1", line=20),
    ]
    rows = emitted(graph, *items)
    with pytest.raises(ValueError, match="sorted"):
        list(summarize_sorted_term_supports(rows))


def test_summary_rejects_bad_polarity(graph):
    from dataclasses import replace

    row = emitted(graph, source())[0]
    bad = replace(row, polarity="BOGUS")
    with pytest.raises(ValueError, match="polarity"):
        list(summarize_sorted_term_supports([bad]))


def test_does_not_materialize_until_iterated(graph):
    def rows():
        yield source(direct="GO:9999999")

    generated = propagate_projected_assertions(rows(), graph=graph)
    with pytest.raises(ValueError, match="Unknown GO"):
        list(generated)


def test_no_evidence_policy_applied(graph):
    rows = emitted(graph, source(evidence="NAS"))
    assert len(rows) == 3
    assert {r.evidence_code for r in rows} == {"NAS"}


def test_namespaced_aspect_labels_accepted():
    graph = GoClosureIndex(
        {"A": "molecular_function", "B": "molecular_function"},
        [("B", "is_a", "A")],
    )
    assert {r.go_id for r in emitted(graph, source(direct="B"))} == {"A", "B"}


def test_actual_m1_projection_contract():
    """Join real M1 data classes instead of only duck-typed synthetic rows."""
    from probe.records import AnnotationRecord
    from probe.stage08_annotations import (
        NormalizedGafSource,
        SourceAssertion,
        project_annotations,
    )
    from probe.stage08_donors import DonorAssociation, DonorIndex

    raw = (
        "UniProtKB",
        "P11111",
        "Protein",
        "enables",
        "GO:0000002",
        "PMID:1",
        "IDA",
        ".",
        "F",
        "Name",
        ".",
        "protein",
        "taxon:1",
        "20230101",
        "Source",
        ".",
        "",
    )
    origin = NormalizedGafSource("2023_01", "/synthetic/start.gaf", "a" * 64)
    record = AnnotationRecord(
        database="UniProtKB",
        subject_id="P11111",
        symbol="Protein",
        relation="enables",
        term_id="GO:0000002",
        negated=False,
        references=("PMID:1",),
        evidence="IDA",
        with_from=(".",),
        aspect="F",
        name="Name",
        synonyms=(".",),
        object_type="protein",
        taxa=("taxon:1",),
        date="20230101",
        assigned_by="Source",
        extensions=(".",),
        gene_product_form_id="",
        source=origin.path,
        line=42,
    )
    assertions = [SourceAssertion(origin, 42, record, raw)]
    donor = DonorAssociation(
        "2023_01",
        "TARGET1",
        "7",
        ".",
        "P11111",
        "YES",
        "DIRECT_PRIMARY_ACCESSION",
        "EXACT_SEQUENCE",
        "1",
        "PRIMARY",
        "PRIMARY",
        ".",
        "1",
    )
    donors = DonorIndex(
        [donor],
        start_release_id="2023_01",
        end_release_id="2026_03",
        source_database="UniProtKB",
    )
    graph = GoClosureIndex(
        {"GO:0000001": "F", "GO:0000002": "F"},
        [("GO:0000002", "is_a", "GO:0000001")],
    )
    rows = list(
        propagate_projected_assertions(
            project_annotations(assertions, donors=donors), graph=graph
        )
    )
    assert {row.go_id for row in rows} == {"GO:0000001", "GO:0000002"}
    assert {row.origin for row in rows} == {"DIRECT", "INHERITED"}
    assert {row.source_link_key for row in rows} == {
        ("2023_01", "TARGET1", "7", assertions[0].assertion_id)
    }
    assert {row.evidence_code for row in rows} == {"IDA"}
    assert all(row.normalized_gaf_line == 42 for row in rows)
