from __future__ import annotations

import csv
import hashlib
import json

import pytest

from probe.target_preparation import (
    CANONICAL_SEQUENCE_POLICY_VERSION,
    prepare_targets,
)

INVALID_SEQUENCE_REASON = (
    "submitted sequence cannot be verified by strict exact UniProtKB identity "
    "matching in the supplied form"
)


def _read_tsv(path):
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _prepare(tmp_path, fasta_text: str, metadata_rows: list[tuple[str, str, str]]):
    fasta = tmp_path / "targets.fasta"
    metadata = tmp_path / "targets.tsv"
    fasta.write_text(fasta_text, encoding="utf-8")
    with metadata.open("w", encoding="utf-8", newline="\n") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(("target_id", "ncbi_taxid", "uniprot_accession"))
        writer.writerows(metadata_rows)
    output = tmp_path / "output"
    return prepare_targets(
        target_fasta=fasta, metadata_tsv=metadata, output_directory=output
    )


def test_prepare_targets_accepts_literal_v1_symbols_and_writes_identity_fields(
    tmp_path,
):
    sequence = "ACDEFGHIKLMNPQRSTVWYBJOUXZ"
    result = _prepare(tmp_path, f">target-a\n{sequence}\n", [("target-a", "9606", "")])

    assert result.accepted_count == 1
    assert result.rejected_count == 0
    assert _read_tsv(result.targets_path) == [
        {
            "target_id": "target-a",
            "raw_taxid": "9606",
            "optional_accession": "",
            "normalized_sequence": sequence,
            "sequence_length": str(len(sequence)),
            "sequence_sha256": hashlib.sha256(sequence.encode("ascii")).hexdigest(),
            "normalization_policy_version": "protein-sequence-v1",
        }
    ]
    summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
    assert summary["normalization_policy_version"] == CANONICAL_SEQUENCE_POLICY_VERSION


@pytest.mark.parametrize(
    ("sequence", "symbol", "position"),
    [
        ("acdx", "a,c,d,x", "a@1,c@2,d@3,x@4"),
        ("MABCDE*", "*", "*@7"),
        ("MABC*DEF", "*", "*@5"),
        ("MABC-DEF", "-", "-@5"),
        ("MABC.DEF", ".", ".@5"),
        ("MABC?DEF", "?", "?@5"),
        ("MABC_DEF", "_", "_@5"),
    ],
)
def test_prepare_targets_rejects_noncanonical_symbols_without_repair(
    tmp_path, sequence, symbol, position
):
    result = _prepare(tmp_path, f">invalid\n{sequence}\n", [("invalid", "9606", "P1")])

    assert result.accepted_count == 0
    rejected = _read_tsv(result.rejected_path)
    assert rejected == [
        {
            "target_id": "invalid",
            "raw_taxid": "9606",
            "source_line": "2",
            "status": "INVALID_SEQUENCE_SYMBOLS",
            "severity": "ALERT",
            "reason": INVALID_SEQUENCE_REASON,
            "offending_symbols": symbol,
            "offending_positions": position,
        }
    ]
    assert not _read_tsv(result.targets_path)


def test_prepare_targets_audits_coordination_duplicates_and_strict_fasta_layout(
    tmp_path,
):
    result = _prepare(
        tmp_path,
        ">fasta-only\nAAA\n"
        ">missing-taxid\nBBB\n"
        ">duplicate\nCCC\n"
        ">duplicate\nCCC\n"
        ">same-sequence-different-taxid-a\nDDD\n"
        ">same-sequence-different-taxid-b\nDDD\n"
        ">same-sequence-same-taxid-a\nEEE\n"
        ">same-sequence-same-taxid-b\nEEE\n"
        ">header-with-description description\nFFF\n"
        ">multiple-lines\nGG\nHH\n",
        [
            ("missing-taxid", "", ""),
            ("duplicate", "83333", "A0A000"),
            ("duplicate", "83333", "A0A000"),
            ("same-sequence-different-taxid-a", "9606", "P1"),
            ("same-sequence-different-taxid-b", "10090", "P2"),
            ("same-sequence-same-taxid-a", "9606", "P3"),
            ("same-sequence-same-taxid-b", "9606", "P4"),
            ("header-with-description", "9606", ""),
            ("multiple-lines", "9606", ""),
            ("metadata-only", "7227", "Q9"),
        ],
    )

    accepted = _read_tsv(result.targets_path)
    assert [row["target_id"] for row in accepted] == [
        "same-sequence-different-taxid-a",
        "same-sequence-different-taxid-b",
        "same-sequence-same-taxid-a",
        "same-sequence-same-taxid-b",
    ]
    assert [row["raw_taxid"] for row in accepted[:2]] == ["9606", "10090"]
    assert [row["optional_accession"] for row in accepted] == ["P1", "P2", "P3", "P4"]
    rejected = _read_tsv(result.rejected_path)
    assert [(row["target_id"], row["status"]) for row in rejected] == [
        ("fasta-only", "MISSING_METADATA"),
        ("missing-taxid", "MISSING_TAXID"),
        ("duplicate", "DUPLICATE_FASTA_TARGET_ID"),
        ("duplicate", "DUPLICATE_FASTA_TARGET_ID"),
        ("header-with-description", "INVALID_FASTA_HEADER"),
        ("multiple-lines", "INVALID_FASTA_LAYOUT"),
        ("metadata-only", "METADATA_ONLY_TARGET"),
    ]
    assert all(row["severity"] == "ALERT" for row in rejected)
    summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
    assert summary["accepted_count"] == 4
    assert summary["total_fasta_records"] == 10
    assert summary["metadata_only_count"] == 1
    assert summary["rejected_count"] == 7
    assert summary["accepted_count"] + summary["rejected_count"] == (
        summary["total_fasta_records"] + summary["metadata_only_count"]
    )


def test_prepare_targets_rejects_blank_preamble_and_intervening_line(tmp_path):
    with pytest.raises(ValueError, match="precedes first header"):
        _prepare(
            tmp_path,
            "\n>preamble\nACD\n",
            [("preamble", "9606", "")],
        )

    result = _prepare(
        tmp_path,
        ">first\nACD\n\n>second\nEFG\n",
        [("first", "9606", ""), ("second", "9606", "")],
    )

    assert [row["target_id"] for row in _read_tsv(result.targets_path)] == ["second"]
    assert _read_tsv(result.rejected_path) == [
        {
            "target_id": "first",
            "raw_taxid": "9606",
            "source_line": "1",
            "status": "INVALID_FASTA_LAYOUT",
            "severity": "ALERT",
            "reason": "canonical FASTA record must contain exactly one physical "
            "sequence line",
            "offending_symbols": "",
            "offending_positions": "",
        }
    ]


def test_prepare_targets_requires_exact_metadata_schema_and_order(tmp_path):
    fasta = tmp_path / "targets.fasta"
    metadata = tmp_path / "targets.tsv"
    fasta.write_text(">target\nACD\n", encoding="utf-8")
    metadata.write_text(
        "target_id\tncbi_taxid\ntarget\t9606\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="exactly this header and column order"):
        prepare_targets(
            target_fasta=fasta,
            metadata_tsv=metadata,
            output_directory=tmp_path / "output",
        )

    metadata.write_text(
        "ncbi_taxid\ttarget_id\tuniprot_accession\n9606\ttarget\t\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="exactly this header and column order"):
        prepare_targets(
            target_fasta=fasta,
            metadata_tsv=metadata,
            output_directory=tmp_path / "output",
        )


def test_prepare_targets_rejects_duplicate_metadata_target_id(tmp_path):
    result = _prepare(
        tmp_path,
        ">target\nACD\n",
        [("target", "9606", "P1"), ("target", "10090", "P2")],
    )

    assert result.accepted_count == 0
    rejected = _read_tsv(result.rejected_path)
    assert [(row["target_id"], row["status"]) for row in rejected] == [
        ("target", "DUPLICATE_METADATA_TARGET_ID")
    ]


def test_prepare_targets_outputs_are_deterministic_and_accession_is_not_identity(
    tmp_path,
):
    fasta = ">first\nMBOJXZU\n>second\nMBOJXZU\n"
    metadata = [("first", "9606", "P12345"), ("second", "9606", "Q99999")]
    result = _prepare(tmp_path, fasta, metadata)
    first = {
        path.name: path.read_bytes()
        for path in (result.targets_path, result.rejected_path, result.summary_path)
    }
    repeated = prepare_targets(
        target_fasta=tmp_path / "targets.fasta",
        metadata_tsv=tmp_path / "targets.tsv",
        output_directory=tmp_path / "output",
    )
    assert {
        path.name: path.read_bytes()
        for path in (
            repeated.targets_path,
            repeated.rejected_path,
            repeated.summary_path,
        )
    } == first
    records = _read_tsv(result.targets_path)
    assert [record["target_id"] for record in records] == ["first", "second"]
    assert records[0]["sequence_sha256"] == records[1]["sequence_sha256"]
    assert records[0]["optional_accession"] != records[1]["optional_accession"]
