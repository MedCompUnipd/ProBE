"""Synthetic tests for release-qualified annotation projection and summaries."""

import hashlib
from dataclasses import replace

import pytest

from probe.records import AnnotationRecord
from probe.stage08_annotations import (
    NormalizedGafSource,
    ProjectedAssertion,
    SourceAssertion,
    project_annotations,
    summarize_projection,
)
from probe.stage08_donors import DonorAssociation, DonorIndex


def association(
    *,
    release: str = "S",
    target: str = "T1",
    rid: str = "1",
    accession: str = "A",
    donor: str = "YES",
    count: str = "1",
) -> DonorAssociation:
    return DonorAssociation(
        release,
        target,
        rid,
        ".",
        accession,
        donor,
        "DIRECT_PRIMARY_ACCESSION",
        "EXACT_SEQUENCE",
        "1",
        "PRIMARY",
        "PRIMARY",
        ".",
        count,
    )


def index(*rows: DonorAssociation) -> DonorIndex:
    return DonorIndex(
        rows, start_release_id="S", end_release_id="E", source_database="UniProtKB"
    )


def source(release: str = "S") -> NormalizedGafSource:
    # A deterministic synthetic source identity, never a production checksum.
    digest = hashlib.sha256(f"synthetic-normalized-{release}".encode()).hexdigest()
    return NormalizedGafSource(release, f"/synthetic/{release}.gaf", digest)


def assertion(
    *,
    release: str = "S",
    line: int = 10,
    accession: str = "A",
    code: str = "IDA",
    term: str = "GO:0000001",
    qualifier: str = "enables",
    database: str = "UniProtKB",
    reference: str = "PMID:1",
    form: str = "",
    aspect: str = "F",
) -> SourceAssertion:
    origin = source(release)
    raw = (
        database,
        accession,
        "symbol",
        qualifier,
        term,
        reference,
        code,
        "WITH:1|WITH:2",
        aspect,
        'A "quoted" name',
        "alias1|alias2",
        "protein",
        "taxon:1",
        "20230101",
        "SOURCE",
        "extension(x)|extension(y)",
        form,
    )

    def parts(text: str) -> tuple[str, ...]:
        return tuple(value for value in text.split("|") if value)

    relation_parts = parts(qualifier)
    relations = tuple(value for value in relation_parts if value != "NOT")
    record = AnnotationRecord(
        database=database,
        subject_id=accession,
        symbol=raw[2],
        relation=relations[0] if len(relations) == 1 else "",
        term_id=term,
        negated="NOT" in relation_parts,
        references=parts(reference),
        evidence=code,
        with_from=parts(raw[7]),
        aspect=aspect,
        name=raw[9],
        synonyms=parts(raw[10]),
        object_type=raw[11],
        taxa=parts(raw[12]),
        date=raw[13],
        assigned_by=raw[14],
        extensions=parts(raw[15]),
        gene_product_form_id=form,
        source=origin.path,
        line=line,
    )
    return SourceAssertion(origin, line, record, raw)


def summarize(rows: list[SourceAssertion], donors: DonorIndex):
    return summarize_projection(project_annotations(rows, donors=donors), donors=donors)


def test_all_donors_contribute_and_evidence_is_a_set() -> None:
    donors = index(
        association(count="2"), association(rid="2", accession="B", count="2")
    )
    result = summarize(
        [
            assertion(accession="A", line=10, code="IDA"),
            assertion(accession="B", line=20, code="IMP"),
        ],
        donors,
    )
    assert result.source_row_count == 2
    assert result.association_link_count == 2
    assert len(result.direct_groups) == 1
    assert result.direct_groups[0].evidence_codes == frozenset({"IDA", "IMP"})
    assert {link.rid for link in result.direct_groups[0].supports} == {"1", "2"}


def test_one_source_row_many_targets_is_not_counted_twice() -> None:
    donors = index(association(target="T1"), association(target="T2"))
    result = summarize([assertion()], donors)
    assert result.source_row_count == 1
    assert result.association_link_count == 2
    assert {group.target_id for group in result.direct_groups} == {"T1", "T2"}
    inventory = result.evidence_inventory[0]
    assert inventory.positive_source_rows == 1
    assert inventory.association_links == 2
    assert inventory.distinct_donors == 1
    assert inventory.distinct_targets == 2


def test_start_and_end_have_independent_donor_authorizations() -> None:
    donors = index(
        association(release="S", accession="A"),
        association(release="E", accession="B"),
    )
    rows = [
        assertion(release="S", line=10, accession="A", code="IEA"),
        assertion(release="E", line=10, accession="A", code="EXP"),
        assertion(release="S", line=20, accession="B", code="IMP"),
        assertion(release="E", line=20, accession="B", code="IDA"),
    ]
    result = summarize(rows, donors)
    by_release = {group.release: group.evidence_codes for group in result.direct_groups}
    assert by_release == {"S": frozenset({"IEA"}), "E": frozenset({"IDA"})}


def test_same_accession_can_belong_to_different_targets_across_releases() -> None:
    donors = index(association(), association(release="E", target="T2"))
    result = summarize([assertion(), assertion(release="E")], donors)
    assert {(group.release, group.target_id) for group in result.direct_groups} == {
        ("S", "T1"),
        ("E", "T2"),
    }


def test_donor_no_is_kept_but_never_links() -> None:
    donors = index(association(donor="NO"))
    result = summarize([assertion()], donors)
    assert result.direct_groups == ()
    assert result.evidence_inventory == ()
    assert result.source_row_count == 0
    assert result.associations[0].association.annotation_donor == "NO"
    assert result.associations[0].positive_rows_observed == 0


def test_zero_observations_is_not_certified_missing_go() -> None:
    donors = index(association())
    result = summarize([], donors)
    assert len(result.associations) == 1
    assert result.associations[0].positive_rows_observed == 0
    assert result.associations[0].not_rows_observed == 0
    assert result.completeness == "supplied_observations_only"


def test_only_not_is_retrieved_without_positive_conversion() -> None:
    result = summarize([assertion(qualifier="NOT|enables")], index(association()))
    assert result.direct_groups[0].negated is True
    assert result.associations[0].not_rows_observed == 1
    assert result.associations[0].positive_rows_observed == 0
    assert result.evidence_inventory[0].not_source_rows == 1


def test_positive_and_not_same_term_both_survive() -> None:
    rows = [
        assertion(line=10, code="IDA", qualifier="enables"),
        assertion(line=20, code="IEA", qualifier="NOT|enables"),
    ]
    result = summarize(rows, index(association()))
    by_polarity = {
        group.negated: group.evidence_codes for group in result.direct_groups
    }
    assert by_polarity == {False: frozenset({"IDA"}), True: frozenset({"IEA"})}
    assert result.association_link_count == 2


def test_cross_donor_not_does_not_veto_positive() -> None:
    donors = index(
        association(count="2"), association(rid="2", accession="B", count="2")
    )
    result = summarize(
        [
            assertion(line=10, accession="A", code="IDA", form="A-1"),
            assertion(
                line=20, accession="B", code="IEA", qualifier="NOT|enables", form="B-2"
            ),
        ],
        donors,
    )
    assert len(result.direct_groups) == 2
    assert {group.negated for group in result.direct_groups} == {False, True}


def test_new_codes_are_not_filtered_or_classified() -> None:
    rows = [assertion(line=10, code="FUTURE_CODE_X"), assertion(line=20, code="new-x")]
    result = summarize(rows, index(association()))
    assert result.direct_groups[0].evidence_codes == frozenset(
        {"FUTURE_CODE_X", "new-x"}
    )
    assert {item.evidence_code for item in result.evidence_inventory} == {
        "FUTURE_CODE_X",
        "new-x",
    }
    assert result.inventory_scope == "retrieved_donor_assertions"


def test_nas_nd_and_iea_are_not_filtered_at_collection() -> None:
    rows = [
        assertion(line=n, code=code) for n, code in enumerate(["NAS", "ND", "IEA"], 1)
    ]
    result = summarize(rows, index(association()))
    assert result.direct_groups[0].evidence_codes == frozenset({"NAS", "ND", "IEA"})


def test_different_references_preserve_distinct_source_supports() -> None:
    first = assertion(line=10, reference="PMID:1")
    second = assertion(line=11, reference="PMID:2")
    result = summarize([first, second], index(association()))
    group = result.direct_groups[0]
    assert group.evidence_codes == frozenset({"IDA"})
    assert {support.assertion_id for support in group.supports} == {
        first.assertion_id,
        second.assertion_id,
    }


def test_identical_physical_rows_at_different_lines_remain_distinct() -> None:
    first, second = assertion(line=10), assertion(line=11)
    assert first.raw_fields == second.raw_fields
    assert first.assertion_id != second.assertion_id
    result = summarize([first, second], index(association()))
    assert result.source_row_count == 2
    assert len(result.direct_groups[0].supports) == 2


def test_raw_fields_and_existing_record_are_retained() -> None:
    row = assertion(qualifier="NOT|enables", reference="PMID:1|PMID:2", form="A-1")
    item = next(project_annotations([row], donors=index(association())))
    assert item.assertion is row
    assert item.assertion.raw_fields[3] == "NOT|enables"
    assert item.assertion.annotation.references == ("PMID:1", "PMID:2")
    assert item.assertion.raw_fields[9] == 'A "quoted" name'
    assert item.assertion.annotation.gene_product_form_id == "A-1"


@pytest.mark.parametrize("database", ["OtherDB", "sp", "uniprotkb"])
def test_other_database_does_not_join(database: str) -> None:
    result = summarize([assertion(database=database)], index(association()))
    assert result.source_row_count == 0


@pytest.mark.parametrize("accession", ["A-1", "a", "UniProtKB:A"])
def test_no_accession_alias_expansion(accession: str) -> None:
    result = summarize([assertion(accession=accession)], index(association()))
    assert result.source_row_count == 0


def test_distinct_targets_never_share_donor_sets() -> None:
    # No sequence input is accepted by this API, even for equal-sequence targets.
    donors = index(
        association(target="T1", accession="A"),
        association(target="T2", rid="2", accession="B"),
    )
    result = summarize(
        [
            assertion(line=10, accession="A", term="GO:0000001"),
            assertion(line=20, accession="B", term="GO:0000002"),
        ],
        donors,
    )
    assert {(group.target_id, group.term_id) for group in result.direct_groups} == {
        ("T1", "GO:0000001"),
        ("T2", "GO:0000002"),
    }


def test_multiple_aspects_remain_separate_and_inventory_reports_them() -> None:
    rows = [assertion(line=10), assertion(line=20, aspect="P", term="GO:0000002")]
    result = summarize(rows, index(association()))
    assert {group.aspect for group in result.direct_groups} == {"F", "P"}
    assert result.evidence_inventory[0].aspects == ("F", "P")


def test_unrelated_rows_do_not_enter_donor_inventory() -> None:
    rows = [assertion(line=10), assertion(line=20, accession="Z", code="UNRELATED")]
    result = summarize(rows, index(association()))
    assert {item.evidence_code for item in result.evidence_inventory} == {"IDA"}


def test_original_line_numbers_survive_prefiltering() -> None:
    row = assertion(line=123456)
    item = next(project_annotations([row], donors=index(association())))
    assert item.assertion.line_number == 123456
    assert item.assertion.annotation.line == 123456


def test_subset_line_number_cannot_replace_original_line() -> None:
    row = assertion(line=123456)
    with pytest.raises(ValueError, match="source/line"):
        replace(row, line_number=1)


def test_assertion_id_is_independent_of_local_mount_path() -> None:
    first = assertion()
    second = replace(
        first,
        source=replace(first.source, path="/another/location.gaf"),
        annotation=replace(first.annotation, source="/another/location.gaf"),
    )
    assert first.assertion_id == second.assertion_id


def test_assertion_id_is_release_and_source_qualified() -> None:
    first = assertion()
    second = replace(first, source=replace(first.source, release="E"))
    digest = hashlib.sha256(b"a different normalized source").hexdigest()
    third = replace(first, source=replace(first.source, sha256=digest))
    assert len({first.assertion_id, second.assertion_id, third.assertion_id}) == 3


def test_unknown_release_is_not_silently_dropped() -> None:
    with pytest.raises(ValueError, match="unknown annotation release"):
        summarize([assertion(release="OTHER")], index(association()))


def test_two_gaf_sources_for_one_release_are_rejected() -> None:
    first, second = assertion(line=10), assertion(line=20)
    second = replace(second, source=replace(second.source, sha256="f" * 64))
    with pytest.raises(ValueError, match="mix GAF sources"):
        summarize([first, second], index(association()))


@pytest.mark.parametrize("lines", [(10, 10), (20, 10)])
def test_duplicate_or_decreasing_source_lines_are_rejected(lines) -> None:
    with pytest.raises(ValueError, match="source line"):
        summarize([assertion(line=n) for n in lines], index(association()))


def test_projector_is_lazy() -> None:
    visited = []

    def incoming():
        visited.append(1)
        yield assertion(line=10)
        visited.append(2)
        yield assertion(line=20)

    iterator = project_annotations(incoming(), donors=index(association()))
    assert visited == []
    next(iterator)
    assert visited == [1]


def test_input_generator_failure_is_not_swallowed() -> None:
    def incoming():
        yield assertion()
        raise RuntimeError("upstream read failed")

    with pytest.raises(RuntimeError, match="upstream read failed"):
        summarize_projection(
            project_annotations(incoming(), donors=index(association())),
            donors=index(association()),
        )


def test_summary_cannot_hide_a_donor_by_using_only_first_match() -> None:
    donors = index(association(target="T1"), association(target="T2"))
    fake = ProjectedAssertion(assertion(), (donors.associations[0],))
    with pytest.raises(ValueError, match="complete authorized set"):
        summarize_projection([fake], donors=donors)


def test_summary_rejects_duplicate_source_id() -> None:
    donors = index(association())
    item = next(project_annotations([assertion()], donors=donors))
    with pytest.raises(ValueError, match="duplicate source assertion"):
        summarize_projection([item, item], donors=donors)


def test_summary_is_independent_of_projected_record_order() -> None:
    donors = index(association())
    items = list(
        project_annotations(
            [
                assertion(line=10, code="IDA"),
                assertion(line=20, code="IMP"),
            ],
            donors=donors,
        )
    )
    assert summarize_projection(items, donors=donors) == summarize_projection(
        reversed(items), donors=donors
    )


@pytest.mark.parametrize("line", [0, -1, True, "10"])
def test_invalid_line_number_rejected(line) -> None:
    with pytest.raises(ValueError, match="line_number"):
        replace(assertion(), line_number=line)


@pytest.mark.parametrize("digest", ["", "x" * 64, "A" * 64, "a" * 63])
def test_invalid_declared_source_digest_rejected(digest: str) -> None:
    with pytest.raises(ValueError, match="SHA256"):
        replace(source(), sha256=digest)


@pytest.mark.parametrize("code", ["", " "])
def test_blank_evidence_is_not_a_new_code(code: str) -> None:
    with pytest.raises(ValueError, match="missing evidence"):
        assertion(code=code)


def test_mismatched_record_and_raw_fields_rejected() -> None:
    row = assertion()
    with pytest.raises(ValueError, match="disagrees with retained raw"):
        replace(row, annotation=replace(row.annotation, evidence="IMP"))


def test_malformed_raw_field_count_rejected() -> None:
    with pytest.raises(ValueError, match="17 GAF fields"):
        replace(assertion(), raw_fields=("too few",))


def test_multiple_relations_rejected() -> None:
    with pytest.raises(ValueError, match="multiple non-NOT"):
        assertion(qualifier="enables|involved_in")


def test_empty_donor_index_drops_rows_without_fabricating_targets() -> None:
    result = summarize([assertion()], index())
    assert result.direct_groups == ()
    assert result.associations == ()
    assert result.evidence_inventory == ()
