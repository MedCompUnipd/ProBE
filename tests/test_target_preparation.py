from __future__ import annotations

import csv
import hashlib
import json

import pytest

from probe.target_preparation import prepare_targets


def _read_tsv(path):
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def test_prepare_targets_writes_deterministic_accepted_and_rejected_outputs(tmp_path):
    fasta = tmp_path / "targets.fasta"
    metadata = tmp_path / "targets.tsv"
    output = tmp_path / "output"
    fasta.write_text(
        ">valid\nacdxu\n"
        ">missing-metadata\nMNP\n"
        ">missing-taxid\nAAA\n"
        ">invalid-sequence\nAC*D\n"
        ">duplicate\nCCC\n"
        ">duplicate\nCCC\n",
        encoding="utf-8",
    )
    metadata.write_text(
        "target_id\tncbi_taxid\tuniprot_accession\n"
        "valid\t9606\tP12345\n"
        "missing-taxid\t\t\n"
        "invalid-sequence\t10090\t\n"
        "duplicate\t83333\tA0A000\n",
        encoding="utf-8",
    )

    result = prepare_targets(
        target_fasta=fasta, metadata_tsv=metadata, output_directory=output
    )

    assert result.accepted_count == 1
    assert result.rejected_count == 5
    targets = _read_tsv(result.targets_path)
    assert [
        {key: value for key, value in targets[0].items() if key != "sequence_sha256"}
    ] == [
        {
            "target_id": "valid",
            "raw_taxid": "9606",
            "optional_accession": "P12345",
            "normalized_sequence": "ACDXU",
            "sequence_length": "5",
            "normalization_policy_version": "1",
        }
    ]
    assert targets[0]["sequence_sha256"] == hashlib.sha256(b"ACDXU").hexdigest()
    rejected = _read_tsv(result.rejected_path)
    assert [(row["target_id"], row["status"]) for row in rejected] == [
        ("missing-metadata", "MISSING_METADATA"),
        ("missing-taxid", "MISSING_TAXID"),
        ("invalid-sequence", "INVALID_SEQUENCE"),
        ("duplicate", "DUPLICATE_TARGET_ID"),
        ("duplicate", "DUPLICATE_TARGET_ID"),
    ]
    summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
    assert summary == {
        "accepted_count": 1,
        "metadata_path": str(metadata),
        "normalization_policy_version": "1",
        "rejected_count": 5,
        "target_fasta_path": str(fasta),
        "target_sequence_declared_full_length": True,
        "total_fasta_records": 6,
    }

    first_outputs = {
        path.name: path.read_bytes()
        for path in (result.targets_path, result.rejected_path, result.summary_path)
    }
    prepare_targets(target_fasta=fasta, metadata_tsv=metadata, output_directory=output)
    assert {
        path.name: path.read_bytes()
        for path in (result.targets_path, result.rejected_path, result.summary_path)
    } == first_outputs


def test_prepare_targets_rejects_conflicting_duplicate_metadata(tmp_path):
    fasta = tmp_path / "targets.fasta"
    metadata = tmp_path / "targets.tsv"
    fasta.write_text(">target\nACD\n", encoding="utf-8")
    metadata.write_text(
        "target_id\tncbi_taxid\ntarget\t9606\ntarget\t10090\n",
        encoding="utf-8",
    )

    result = prepare_targets(
        target_fasta=fasta,
        metadata_tsv=metadata,
        output_directory=tmp_path / "output",
    )

    assert result.accepted_count == 0
    assert _read_tsv(result.rejected_path)[0]["status"] == "CONFLICTING_METADATA"


def test_prepare_targets_rejects_repeated_duplicate_metadata(tmp_path):
    fasta = tmp_path / "targets.fasta"
    metadata = tmp_path / "targets.tsv"
    fasta.write_text(">target\nACD\n", encoding="utf-8")
    metadata.write_text(
        "target_id\tncbi_taxid\ntarget\t9606\ntarget\t9606\n",
        encoding="utf-8",
    )

    result = prepare_targets(
        target_fasta=fasta,
        metadata_tsv=metadata,
        output_directory=tmp_path / "output",
    )

    assert result.accepted_count == 0
    assert _read_tsv(result.rejected_path)[0]["status"] == "DUPLICATE_METADATA"


def test_prepare_targets_requires_metadata_columns(tmp_path):
    fasta = tmp_path / "targets.fasta"
    metadata = tmp_path / "targets.tsv"
    fasta.write_text(">target\nACD\n", encoding="utf-8")
    metadata.write_text("target_id\tspecies\ntarget\tHuman\n", encoding="utf-8")

    with pytest.raises(ValueError, match="requires target_id and ncbi_taxid"):
        prepare_targets(
            target_fasta=fasta,
            metadata_tsv=metadata,
            output_directory=tmp_path / "output",
        )
