"""Synthetic tests of the Stage 07 -> Stage 08 donor boundary."""

from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from probe.stage08_donors import (
    ACCEPTED_COLUMNS,
    DonorAssociation,
    DonorIndex,
    read_accepted_associations,
)


def association(
    *,
    release: str = "start-release",
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


def index(rows: list[DonorAssociation]) -> DonorIndex:
    return DonorIndex(
        rows,
        start_release_id="start-release",
        end_release_id="end-release",
        source_database="UniProtKB",
    )


def write_rows(path: Path, rows: list[DonorAssociation]) -> None:
    text = "\t".join(ACCEPTED_COLUMNS) + "\r\n"
    text += "".join("\t".join(row.tsv_values()) + "\r\n" for row in rows)
    path.write_bytes(text.encode("utf-8"))


def read(path: Path) -> DonorIndex:
    return read_accepted_associations(
        path,
        start_release_id="start-release",
        end_release_id="end-release",
        source_database="UniProtKB",
    )


def test_read_retains_every_original_field(tmp_path: Path) -> None:
    path = tmp_path / "accepted.tsv"
    row = replace(association(rid="001"), target_accession="P12345")
    write_rows(path, [row])
    original = path.read_bytes()
    result = read(path)
    assert result.associations == (row,)
    assert result.associations[0].tsv_values() == row.tsv_values()
    assert path.read_bytes() == original


def test_header_only_is_a_valid_empty_projection(tmp_path: Path) -> None:
    path = tmp_path / "accepted.tsv"
    write_rows(path, [])
    result = read(path)
    assert result.associations == ()
    assert result.donor_accessions("start-release") == frozenset()


def test_all_yes_and_no_remain_in_accepted_multiplicity() -> None:
    rows = [
        association(rid="1", accession="A", count="3"),
        association(rid="2", accession="B", count="3"),
        association(rid="3", accession="C", donor="NO", count="3"),
    ]
    result = index(rows)
    assert result.associations == tuple(rows)
    assert result.donor_accessions("start-release") == frozenset({"A", "B"})
    assert not result.donors_for(
        release="start-release", database="UniProtKB", accession="C"
    )
    assert all(row.declared_multiplicity == 3 for row in result.associations)


def test_same_record_can_donate_to_multiple_targets() -> None:
    rows = [association(target="T1"), association(target="T2")]
    result = index(rows)
    matches = result.donors_for(
        release="start-release", database="UniProtKB", accession="A"
    )
    assert {row.target_id for row in matches} == {"T1", "T2"}


def test_lookup_requires_release_and_database() -> None:
    first = association()
    second = association(release="end-release", target="T2", accession="A")
    result = index([first, second])
    assert result.donors_for(
        release="start-release", database="UniProtKB", accession="A"
    ) == (first,)
    assert result.donors_for(
        release="end-release", database="UniProtKB", accession="A"
    ) == (second,)
    assert (
        result.donors_for(release="start-release", database="OtherDB", accession="A")
        == ()
    )


@pytest.mark.parametrize("accession", ["a", "A-1", "UniProtKB:A", " A", "A "])
def test_lookup_has_no_identifier_repairs(accession: str) -> None:
    assert (
        index([association()]).donors_for(
            release="start-release", database="UniProtKB", accession=accession
        )
        == ()
    )


def test_no_extra_sequence_or_scope_gate_is_added() -> None:
    row = replace(
        association(),
        sequence_relation="OTHER_NONEXACT_RELATION",
        exact_sequence="0",
        scope="TAXONOMY_AUDIT",
        evaluation_scope="PRIMARY",
        audit_outcome="PERMITTED_BY_EXPLICIT_UPSTREAM_POLICY",
        decision_basis="DIRECT_ACCESSION_CONTINUITY",
    )
    result = index([row])
    assert result.donors_for(
        release="start-release", database="UniProtKB", accession="A"
    ) == (row,)


def test_order_does_not_change_index() -> None:
    rows = [
        association(target="Z", rid="8", accession="B"),
        association(target="A", rid="1", accession="A"),
    ]
    assert index(rows) == index(list(reversed(rows)))


def test_index_and_rows_are_immutable() -> None:
    result = index([association()])
    with pytest.raises(TypeError):
        result.by_key[("x", "y", "z")] = association()
    with pytest.raises(FrozenInstanceError):
        result.associations[0].annotation_donor = "NO"


def test_duplicate_key_is_error_even_if_identical() -> None:
    with pytest.raises(ValueError, match="duplicate association"):
        index([association(), association()])


def test_inconsistent_rid_accession_is_error() -> None:
    with pytest.raises(ValueError, match="inconsistent accession"):
        index([association(target="T1"), association(target="T2", accession="B")])


def test_declared_count_is_not_yes_only() -> None:
    with pytest.raises(ValueError, match="multiplicity mismatch"):
        index([association(count="2")])


def test_conflicting_declared_counts_are_error() -> None:
    with pytest.raises(ValueError, match="inconsistent declared multiplicity"):
        index(
            [
                association(count="2"),
                association(rid="2", accession="B", count="1"),
            ]
        )


@pytest.mark.parametrize("operation", ["row", "lookup", "role", "accessions"])
def test_unknown_release_is_error(operation: str) -> None:
    with pytest.raises(ValueError, match="unknown .*release"):
        if operation == "row":
            index([association(release="OTHER")])
        elif operation == "lookup":
            index([]).donors_for(release="OTHER", database="UniProtKB", accession="A")
        elif operation == "role":
            index([]).role_for("OTHER")
        else:
            index([]).donor_accessions("OTHER")


def test_roles_cannot_have_same_release_id() -> None:
    with pytest.raises(ValueError, match="distinct releases"):
        DonorIndex(
            [],
            start_release_id="same",
            end_release_id="same",
            source_database="UniProtKB",
        )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("annotation_donor", "yes"),
        ("exact_sequence", "true"),
        ("scope", "unknown"),
        ("evaluation_scope", "unknown"),
        ("candidate_accession", "."),
        ("target_id", ""),
        ("rid", "0"),
        ("rid", "-1"),
        ("accepted_candidates_for_target_release", "0"),
        ("decision_basis", " bad"),
        ("audit_outcome", "bad\tvalue"),
    ],
)
def test_invalid_structural_fields_fail(name: str, value: str) -> None:
    with pytest.raises(ValueError):
        replace(association(), **{name: value})


@pytest.mark.parametrize("kind", ["empty", "renamed", "duplicate", "reordered"])
def test_bad_header_is_error(tmp_path: Path, kind: str) -> None:
    path = tmp_path / "bad.tsv"
    columns = list(ACCEPTED_COLUMNS)
    if kind == "empty":
        path.write_text("")
    else:
        if kind == "renamed":
            columns[0] = "role"
        elif kind == "duplicate":
            columns[1] = columns[0]
        else:
            columns[0], columns[1] = columns[1], columns[0]
        path.write_text("\t".join(columns) + "\n")
    with pytest.raises(ValueError, match="header"):
        read(path)


@pytest.mark.parametrize("row", ["", "too\tfew", "\t".join(["x"] * 14)])
def test_bad_row_width_is_error(tmp_path: Path, row: str) -> None:
    path = tmp_path / "bad.tsv"
    path.write_text("\t".join(ACCEPTED_COLUMNS) + "\n" + row + "\n")
    with pytest.raises(ValueError, match="expected 13 fields"):
        read(path)
