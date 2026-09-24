from __future__ import annotations

import gzip

import pytest

from probe.identity import sequence_digest
from probe.parsing.uniprot import UniProtDatParser, UniProtSection
from probe.validation import ValidationError, ValidationReport

SWISS_PROT_RECORD = """\
ID   TEST_HUMAN              Reviewed;          10 AA.
AC   P12345; Q11111;
AC   Q22222;
DE   RecName: Full=Synthetic Swiss-Prot protein;
GN   Name=GENE1; Synonyms=ALT1, ALT2;
GN   OrderedLocusNames=LOC1, LOC2; ORFNames=ORF1;
OX   NCBI_TaxID=9606;
SQ   SEQUENCE   10 AA;  1000 MW;  ABCDEF CRC64;
     ACDEFGHIKL        10
//
"""


TREMBL_FRAGMENT_RECORD = """\
ID   TEST_ECOLI              Unreviewed;        6 AA.
AC   A0A000;
DE   RecName: Full=Synthetic TrEMBL protein;
DE   Flags: Fragment;
GN   Name=geneA; ORFNames=ORF_001;
OX   NCBI_TaxID=83333;
SQ   SEQUENCE   6 AA;  600 MW;  FEDCBA CRC64;
     MNPQRS         6
//
"""


def test_swiss_prot_record_extracts_accessions_taxid_genes_and_identity(tmp_path):
    path = tmp_path / "uniprot_sprot.dat"
    path.write_text(SWISS_PROT_RECORD, encoding="utf-8")

    records = UniProtDatParser(UniProtSection.SWISS_PROT).parse(path)

    assert len(records) == 1
    record = records[0]
    assert record.primary_accession == "P12345"
    assert record.secondary_accessions == ("Q11111", "Q22222")
    assert record.entry_name == "TEST_HUMAN"
    assert record.sequence == "ACDEFGHIKL"
    assert record.sequence_length == 10
    assert record.sequence_sha256 == sequence_digest("ACDEFGHIKL")
    assert record.raw_taxid == "9606"
    assert record.section is UniProtSection.SWISS_PROT
    assert record.gene_name == "GENE1"
    assert record.gene_synonyms == ("ALT1", "ALT2")
    assert record.ordered_locus_names == ("LOC1", "LOC2")
    assert record.orf_names == ("ORF1",)
    assert not record.is_fragment


def test_trembl_fragment_record_and_gzip_input(tmp_path):
    path = tmp_path / "uniprot_trembl.dat.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(TREMBL_FRAGMENT_RECORD)

    (record,) = UniProtDatParser(UniProtSection.TREMBL).parse(path)

    assert record.primary_accession == "A0A000"
    assert record.section is UniProtSection.TREMBL
    assert record.raw_taxid == "83333"
    assert record.gene_name == "geneA"
    assert record.orf_names == ("ORF_001",)
    assert record.is_fragment


def test_multiple_records_stream_and_keep_identical_sequences_by_taxid(tmp_path):
    second = (
        SWISS_PROT_RECORD.replace("TEST_HUMAN", "TEST_MOUSE")
        .replace("P12345; Q11111;", "P99999;")
        .replace("AC   Q22222;\n", "")
        .replace("NCBI_TaxID=9606", "NCBI_TaxID=10090")
    )
    path = tmp_path / "two.dat"
    path.write_text(SWISS_PROT_RECORD + second, encoding="utf-8")
    iterator = UniProtDatParser(UniProtSection.SWISS_PROT).iter_records(path)

    first = next(iterator)
    second_record = next(iterator)
    with pytest.raises(StopIteration):
        next(iterator)

    assert first.sequence_sha256 == second_record.sequence_sha256
    assert first.raw_taxid == "9606"
    assert second_record.raw_taxid == "10090"
    assert first.primary_accession != second_record.primary_accession


def test_sequence_length_mismatch_is_rejected_and_reported(tmp_path):
    path = tmp_path / "bad_length.dat"
    path.write_text(
        SWISS_PROT_RECORD.replace(
            "Reviewed;          10 AA.", "Reviewed;          11 AA."
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="UNIPROT_SEQUENCE_LENGTH_MISMATCH"):
        UniProtDatParser(UniProtSection.SWISS_PROT).parse(path)

    report = ValidationReport()
    records = UniProtDatParser(UniProtSection.SWISS_PROT).parse(
        path, report=report, strict=False
    )
    assert records == ()
    assert [issue.code for issue in report.errors] == [
        "UNIPROT_SEQUENCE_LENGTH_MISMATCH"
    ]
