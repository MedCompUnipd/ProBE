from __future__ import annotations

import gzip

import pytest

from probe import SequenceDataset, SequenceIndex, ValidationError
from probe.parsing.gaf import GafParser
from probe.validation import ValidationReport


def test_fasta_normalizes_sequences_and_matches_uniprot_headers(tmp_path):
    targets_path = tmp_path / "targets.fasta"
    targets_path.write_text(">T1 target\nac dU\n>T2\nVVVV\n", encoding="utf-8")
    uniprot_path = tmp_path / "uniprot.fasta.gz"
    with gzip.open(uniprot_path, "wt", encoding="utf-8") as handle:
        handle.write(">sp|P12345|PROTEIN Example\nACDU\n")
        handle.write(">tr|A0A000|PROTEIN Alias\nACDU\n")

    targets = SequenceDataset.read(targets_path)
    index = SequenceIndex.from_fasta(uniprot_path, source_name="uniprot_2023")
    identities = index.match(targets)

    assert targets.records[0].sequence == "ACDU"
    assert [alias.identifier for alias in identities.matches[0].aliases] == [
        "A0A000",
        "P12345",
    ]
    assert identities.unmatched_targets == ("T2",)


def test_fasta_strict_mode_reports_source_line(tmp_path):
    path = tmp_path / "invalid.fasta"
    path.write_text(">T1\nAC*D\n", encoding="utf-8")

    with pytest.raises(ValidationError) as caught:
        SequenceDataset.read(path)

    issue = caught.value.issues[0]
    assert issue.code == "INVALID_AMINO_ACID"
    assert issue.line == 1
    assert issue.source == str(path)


def test_gaf_retains_assertion_provenance(tmp_path):
    path = tmp_path / "annotations.gaf"
    row = "\t".join(
        [
            "UniProtKB",
            "P12345",
            "GENE",
            "NOT|enables",
            "GO:0000001",
            "PMID:1",
            "IDA",
            "UniProtKB:P99999",
            "F",
            "protein name",
            "ALIAS1|ALIAS2",
            "protein",
            "taxon:9606",
            "20230101",
            "UniProt",
            "occurs_in(CL:1)",
            "UniProtKB:P12345-2",
        ]
    )
    path.write_text(f"!gaf-version: 2.2\n{row}\n", encoding="utf-8")
    report = ValidationReport()

    records = GafParser().parse(path, report=report)

    assert report.is_valid
    assert len(records) == 1
    annotation = records[0]
    assert annotation.negated
    assert annotation.relation == "enables"
    assert annotation.references == ("PMID:1",)
    assert annotation.gene_product_form_id == "UniProtKB:P12345-2"
