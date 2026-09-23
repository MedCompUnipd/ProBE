from __future__ import annotations

from pathlib import Path

from probe.preprocessing import main, preprocess_release


def _row(
    accession: str,
    term_id: str,
    *,
    aspect: str = "P",
    qualifier: str = "involved_in",
    database: str = "UniProtKB",
) -> str:
    return "\t".join(
        [
            database,
            accession,
            accession,
            qualifier,
            term_id,
            "PMID:1",
            "IDA",
            "",
            aspect,
            accession,
            "",
            "protein",
            "taxon:9606",
            "20260101",
            "UniProt",
            "",
            "",
        ]
    )


def _write_ontology(path) -> None:
    path.write_text(
        """<?xml version="1.0"?>
<rdf:RDF
  xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
  xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"
  xmlns:owl="http://www.w3.org/2002/07/owl#"
  xmlns:obo="http://purl.obolibrary.org/obo/"
  xmlns:oboInOwl="http://www.geneontology.org/formats/oboInOwl#">
  <owl:Ontology rdf:about="http://purl.obolibrary.org/obo/go.owl">
    <owl:versionIRI rdf:resource="http://purl.obolibrary.org/obo/go/releases/2026-01-01/go.owl"/>
  </owl:Ontology>
  <owl:Class rdf:about="http://purl.obolibrary.org/obo/GO_0008150">
    <oboInOwl:hasOBONamespace>biological_process</oboInOwl:hasOBONamespace>
  </owl:Class>
  <owl:Class rdf:about="http://purl.obolibrary.org/obo/GO_0003674">
    <oboInOwl:hasOBONamespace>molecular_function</oboInOwl:hasOBONamespace>
  </owl:Class>
  <owl:Class rdf:about="http://purl.obolibrary.org/obo/GO_0005575">
    <oboInOwl:hasOBONamespace>cellular_component</oboInOwl:hasOBONamespace>
  </owl:Class>
  <owl:Class rdf:about="http://purl.obolibrary.org/obo/GO_0000001">
    <oboInOwl:hasOBONamespace>biological_process</oboInOwl:hasOBONamespace>
    <oboInOwl:hasAlternativeId>GO:1234567</oboInOwl:hasAlternativeId>
    <rdfs:subClassOf rdf:resource="http://purl.obolibrary.org/obo/GO_0008150"/>
  </owl:Class>
  <owl:Class rdf:about="http://purl.obolibrary.org/obo/GO_0000002">
    <rdfs:label>obsolete old term</rdfs:label>
    <oboInOwl:hasOBONamespace>biological_process</oboInOwl:hasOBONamespace>
    <owl:deprecated>true</owl:deprecated>
    <obo:IAO_0100001 rdf:resource="http://purl.obolibrary.org/obo/GO_0000001"/>
  </owl:Class>
  <owl:Class rdf:about="http://purl.obolibrary.org/obo/GO_0000003">
    <rdfs:label>obsolete without replacement</rdfs:label>
    <oboInOwl:hasOBONamespace>biological_process</oboInOwl:hasOBONamespace>
    <owl:deprecated>true</owl:deprecated>
  </owl:Class>
</rdf:RDF>
""",
        encoding="utf-8",
    )


def test_preprocessor_remaps_purges_filters_and_logs_missing_accessions(tmp_path):
    ontology = tmp_path / "go.owl"
    goa = tmp_path / "release.gaf"
    fasta = tmp_path / "uniprot.fasta"
    cleaned_goa = tmp_path / "GOA_filtered.gaf"
    filtered_fasta = tmp_path / "Uniprot_filtered.fasta"
    debug_log = tmp_path / "preprocess_debug.tsv"
    _write_ontology(ontology)
    goa.write_text(
        "\n".join(
            [
                "!gaf-version: 2.2",
                "!generated-by: synthetic-test",
                _row("P_SPECIFIC", "GO:1234567"),
                _row("P_SPECIFIC", "GO:0008150"),
                _row("P_REPLACED", "GO:0000002"),
                _row("P_NOT", "GO:0000001", qualifier="NOT|involved_in"),
                _row("P_ROOT_ONLY", "GO:0008150"),
                _row("P_OBSOLETE", "GO:0000003"),
                _row("P_UNKNOWN", "GO:9999999"),
                _row("P_MISSING", "GO:0000001"),
                "",
            ]
        ),
        encoding="utf-8",
    )
    fasta.write_text(
        """>sp|P_SPECIFIC|SPECIFIC Protein specific
AAAA
>tr|P_REPLACED|REPLACED Protein replaced
CCCC
>sp|P_ROOT_ONLY|ROOT Protein root only
DDDD
>sp|P_NOT|NOT Protein negative annotation
FFFF
>sp|P_OBSOLETE|OBSOLETE Protein obsolete annotation
GGGG
>sp|P_UNKNOWN|UNKNOWN Protein unknown annotation
HHHH
>sp|P_UNANNOTATED|NONE Protein without GOA
EEEE
""",
        encoding="utf-8",
    )

    result = preprocess_release(
        ontology_path=ontology,
        goa_path=goa,
        uniprot_fasta_path=fasta,
        cleaned_goa_path=cleaned_goa,
        filtered_fasta_path=filtered_fasta,
        debug_log_path=debug_log,
        materialize_accessions=True,
    )

    output_lines = cleaned_goa.read_text(encoding="utf-8").splitlines()
    data_rows = [line.split("\t") for line in output_lines if not line.startswith("!")]
    assert output_lines[:2] == [
        "!gaf-version: 2.2",
        "!generated-by: synthetic-test",
    ]
    assert [(row[1], row[4]) for row in data_rows] == [
        ("P_SPECIFIC", "GO:0000001"),
        ("P_REPLACED", "GO:0000001"),
        ("P_NOT", "GO:0000001"),
    ]
    assert data_rows[-1][3] == "NOT|involved_in"
    assert filtered_fasta.read_text(encoding="utf-8") == (
        ">sp|P_SPECIFIC|SPECIFIC Protein specific\nAAAA\n"
        ">tr|P_REPLACED|REPLACED Protein replaced\nCCCC\n"
        ">sp|P_NOT|NOT Protein negative annotation\nFFFF\n"
    )
    assert debug_log.read_text(encoding="utf-8").splitlines() == ["P_MISSING"]
    assert result.valid_goa_accessions == frozenset(
        {"P_NOT", "P_REPLACED", "P_SPECIFIC"}
    )
    assert result.fasta_accessions_written == frozenset(
        {"P_NOT", "P_REPLACED", "P_SPECIFIC"}
    )
    assert result.remapped_annotation_count == 2
    assert result.invalid_annotation_count == 2
    assert result.root_only_accessions == ("P_ROOT_ONLY",)
    assert result.missing_fasta_accessions == ("P_MISSING",)
    assert result.valid_goa_accession_count == 3
    assert result.fasta_accession_count == 3
    assert result.root_only_accession_count == 1
    assert result.missing_fasta_accession_count == 1
    assert result.shared_accession_count == 6
    assert result.goa_only_accession_count == 1
    assert result.uniprot_only_accession_count == 1


def test_preprocessor_rejects_gaf_aspect_mismatch(tmp_path):
    ontology = tmp_path / "go.owl"
    goa = tmp_path / "release.gaf"
    fasta = tmp_path / "uniprot.fasta"
    _write_ontology(ontology)
    goa.write_text(
        f"!gaf-version: 2.2\n{_row('P1', 'GO:0000001', aspect='F')}\n",
        encoding="utf-8",
    )
    fasta.write_text(">sp|P1|P1\nAAAA\n", encoding="utf-8")

    result = preprocess_release(
        ontology_path=ontology,
        goa_path=goa,
        uniprot_fasta_path=fasta,
        cleaned_goa_path=tmp_path / "cleaned.gaf",
        filtered_fasta_path=tmp_path / "filtered.fasta",
        debug_log_path=tmp_path / "debug.tsv",
    )

    assert result.valid_goa_accessions is None
    assert result.fasta_accessions_written is None
    assert result.root_only_accessions is None
    assert result.missing_fasta_accessions is None
    assert result.valid_goa_accession_count == 0
    assert result.aspect_mismatch_count == 1
    assert (tmp_path / "filtered.fasta").read_text(encoding="utf-8") == ""


def test_cli_filters_sources_and_exports_accession_differences(tmp_path, capsys):
    ontology = tmp_path / "go.owl"
    goa = tmp_path / "release.gaf"
    fasta = tmp_path / "uniprot.fasta"
    new_goa = tmp_path / "new_goa.gaf"
    uni_with_go = tmp_path / "uni_with_go.fasta"
    statistics = tmp_path / "statistics.txt"
    _write_ontology(ontology)
    original_p1 = _row("P1", "GO:1234567")
    changed_p1 = _row("P1", "GO:0000001")
    absent = _row("ABSENT", "GO:0000001")
    complex_portal = _row("CP_ONLY", "GO:0000001", database="ComplexPortal")
    rnacentral = _row("RNA_ONLY", "GO:0000001", database="RNAcentral")
    goa.write_text(
        "\n".join(
            [
                "!gaf-version: 2.2",
                original_p1,
                absent,
                complex_portal,
                rnacentral,
                "",
            ]
        ),
        encoding="utf-8",
    )
    fasta.write_text(
        ">sp|P1|P1\nAAAA\n>sp|UNI_ONLY|UNI_ONLY\nCCCC\n",
        encoding="utf-8",
    )

    exit_code = main(
        [
            "--owl",
            str(ontology),
            "--uniprot",
            str(fasta),
            "--goa",
            str(goa),
            "--new_goa",
            str(new_goa),
            "--uni_with_go",
            str(uni_with_go),
            "--statistics",
            str(statistics),
        ]
    )

    assert exit_code == 0
    assert [
        row.split("\t")[1]
        for row in new_goa.read_text(encoding="utf-8").splitlines()
        if not row.startswith("!")
    ] == ["P1"]
    assert uni_with_go.read_text(encoding="utf-8") == ">sp|P1|P1\nAAAA\n"
    assert (
        Path(f"{new_goa}.missing_accids.log").read_text(encoding="utf-8") == "ABSENT\n"
    )
    assert statistics.read_text(encoding="utf-8") == (
        "### STATISTICS\n"
        "GOA UniProt (shared)\tGOA-only\tUniProt-only\n"
        "1\t1\t1\n\n"
        "### DISCARDED REASONS LEGEND\n"
        "GOA_ONLY: GOA accession is absent from the UniProt FASTA.\n"
        "IGNORED_DB: GOA source database is ComplexPortal or RNAcentral.\n\n"
        "### DISCARDED LINES\n"
        f"{absent}\tGOA_ONLY\n"
        f"{complex_portal}\tIGNORED_DB\n"
        f"{rnacentral}\tIGNORED_DB\n\n"
        "### CHANGED LINES\n"
        f"{changed_p1}\n"
        f"{original_p1}\n"
        "//\n"
    )
    output = capsys.readouterr().out
    assert "GOA ∩ UniProt: 1" in output
    assert "GOA-only: 1" in output
    assert "UniProt-only: 1" in output
    assert not tuple(tmp_path.glob(".probe-preprocess-*.sqlite3"))
