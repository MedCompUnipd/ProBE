"""Synthetic strict-oracle comparison tests, no canonical data required."""

from __future__ import annotations

import csv
import gzip
from dataclasses import dataclass
from pathlib import Path

import pytest

from probe.resolver_assembly import STAGE07_COLUMNS
from probe.resolver_regression import (
    DRY_RUN_COLUMNS,
    _warning_tokens,
    compare_dry_run,
)

KEY = ("2023_01", "T000001", "42")


@dataclass(frozen=True)
class Dummy:
    key: tuple[str, str, str]
    row: dict[str, str]

    def tsv_row(self) -> dict[str, str]:
        return self.row


def make_row(**changes: str) -> dict[str, str]:
    row = {name: "." for name in STAGE07_COLUMNS}
    row.update(
        release=KEY[0],
        target_id=KEY[1],
        rid=KEY[2],
        scope="PRIMARY",
        evaluation_scope="PRIMARY",
        exact_sequence="1",
        decision="ACCEPTED",
        decision_basis="DIRECT_PRIMARY_ACCESSION",
        entity_equivalence="DOCUMENTARY_ACCESSION_CONTINUITY",
        annotation_donor="YES",
        sequence_relation="EXACT_SEQUENCE",
        accepted_candidates_for_target_release="1",
        warnings=".",
        candidate_accession="P12345",
    )
    row.update(changes)
    return row


def write_reference(path: Path, *rows: dict[str, str]) -> None:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=DRY_RUN_COLUMNS, delimiter="\t")
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row[name] for name in DRY_RUN_COLUMNS})


def check(tmp_path: Path, row: dict[str, str], *, reference=None):
    path = tmp_path / "dryrun.tsv.gz"
    write_reference(path, *(reference if reference is not None else [row]))
    return compare_dry_run(
        actual={KEY: Dummy(KEY, row)},
        expected_path=path,
        output_dir=tmp_path / "report",
    )


def test_reference_fields_are_exactly_seventeen():
    assert len(DRY_RUN_COLUMNS) == 17
    assert "evaluation_scope" not in DRY_RUN_COLUMNS
    assert "audit_outcome" not in DRY_RUN_COLUMNS


def test_full_field_agreement(tmp_path):
    report = check(tmp_path, make_row())
    assert report["status"] == "PASS"
    assert report["pairs_with_differences"] == 0


def test_warning_order_is_semantically_ignored(tmp_path):
    observed = make_row(warnings="FRAGMENT_RECORD,MULTIPLE_ACCEPTED_CANDIDATES")
    reference = make_row(warnings="MULTIPLE_ACCEPTED_CANDIDATES,FRAGMENT_RECORD")
    assert check(tmp_path, observed, reference=[reference])["status"] == "PASS"


def test_basis_mismatch_is_detected(tmp_path):
    observed = make_row(decision_basis="EXACT_SEQUENCE_ONLY")
    reference = make_row()
    report = check(tmp_path, observed, reference=[reference])
    assert report["field_difference_counts"] == {"decision_basis": 1}


def test_decision_and_donor_both_compared(tmp_path):
    observed = make_row(decision="UNRESOLVED", annotation_donor="NO")
    report = check(tmp_path, observed, reference=[make_row()])
    assert report["pairs_with_differences"] == 1
    assert report["field_difference_counts"]["decision"] == 1
    assert report["field_difference_counts"]["annotation_donor"] == 1


def test_missing_actual_pair(tmp_path):
    path = tmp_path / "dryrun.tsv"
    write_reference(path, make_row())
    report = compare_dry_run(
        actual={}, expected_path=path, output_dir=tmp_path / "report"
    )
    assert report["field_difference_counts"] == {"MISSING_ACTUAL_PAIR": 1}


def test_extra_actual_pair(tmp_path):
    path = tmp_path / "dryrun.tsv"
    write_reference(path)
    report = compare_dry_run(
        actual={KEY: Dummy(KEY, make_row())},
        expected_path=path,
        output_dir=tmp_path / "report",
    )
    assert report["field_difference_counts"] == {"EXTRA_ACTUAL_PAIR": 1}


def test_duplicate_reference_key_is_error(tmp_path):
    path = tmp_path / "dryrun.tsv"
    write_reference(path, make_row(), make_row())
    with pytest.raises(ValueError, match="Duplicate reference key"):
        compare_dry_run(
            actual={KEY: Dummy(KEY, make_row())},
            expected_path=path,
            output_dir=tmp_path / "report",
        )


def test_refuse_to_overwrite_report(tmp_path):
    (tmp_path / "report").mkdir()
    with pytest.raises(FileExistsError):
        check(tmp_path, make_row())


@pytest.mark.parametrize(
    "token",
    ["A,A", "A,", ",A", "A, B", " A", "A ", "A,,B"],
)
def test_malformed_warning_token(token):
    with pytest.raises(ValueError):
        _warning_tokens(token)


def test_empty_warnings():
    assert _warning_tokens(".") == ()
    assert _warning_tokens("") == ()


def test_example_cap(tmp_path):
    path = tmp_path / "dryrun.tsv"
    write_reference(path, make_row())
    report = compare_dry_run(
        actual={KEY: Dummy(KEY, make_row(decision="UNRESOLVED", warnings="X"))},
        expected_path=path,
        output_dir=tmp_path / "report",
        max_examples=1,
    )
    assert report["pairs_with_differences"] == 1
    assert report["examples_written"] == 1
    assert report["field_difference_counts"]["warnings"] == 1
    assert report["field_difference_counts"]["decision"] == 1


def test_reject_negative_example_cap(tmp_path):
    with pytest.raises(ValueError, match="max_examples"):
        compare_dry_run(
            actual={},
            expected_path=tmp_path / "none.tsv",
            output_dir=tmp_path / "report",
            max_examples=-1,
        )
