from __future__ import annotations

import hashlib

import pytest

from probe import (
    AxiomOrigin,
    EdgeDispositionKind,
    OwlLoader,
    TermStatus,
    ValidationError,
)


def _write_owl(tmp_path, body: str, *, name: str = "go.owl"):
    path = tmp_path / name
    path.write_text(
        f"""<?xml version="1.0"?>
<rdf:RDF
  xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
  xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"
  xmlns:owl="http://www.w3.org/2002/07/owl#"
  xmlns:obo="http://purl.obolibrary.org/obo/"
  xmlns:oboInOwl="http://www.geneontology.org/formats/oboInOwl#">
  <owl:Ontology rdf:about="http://purl.obolibrary.org/obo/go.owl">
    <owl:versionIRI rdf:resource="http://purl.obolibrary.org/obo/go/releases/2025-02-06/go.owl"/>
    <owl:imports rdf:resource="http://example.org/pinned-import.owl"/>
  </owl:Ontology>
  {body}
</rdf:RDF>
""",
        encoding="utf-8",
    )
    return path


def _checksum(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_context_rejects_checksum_mismatch_before_parsing(tmp_path):
    path = tmp_path / "broken.owl"
    path.write_text("not XML", encoding="utf-8")

    with pytest.raises(ValidationError) as error:
        OwlLoader().load_context(
            path,
            expected_sha256="0" * 64,
            source_url="http://purl.obolibrary.org/obo/go.owl",
            retrieval_date="2026-09-18",
        )

    assert [issue.code for issue in error.value.issues] == ["CHECKSUM_MISMATCH"]


def test_context_preserves_graph_metadata_external_and_unknown_edges(tmp_path):
    path = _write_owl(
        tmp_path,
        """
  <owl:ObjectProperty rdf:about="http://example.org/unknown_relation">
    <rdfs:label>looks like part of but is not BFO part_of</rdfs:label>
  </owl:ObjectProperty>
  <owl:Class rdf:about="http://purl.obolibrary.org/obo/GO_0008150">
    <oboInOwl:hasOBONamespace>biological_process</oboInOwl:hasOBONamespace>
  </owl:Class>
  <owl:Class rdf:about="http://purl.obolibrary.org/obo/GO_0000001">
    <oboInOwl:hasOBONamespace>biological_process</oboInOwl:hasOBONamespace>
    <rdfs:subClassOf rdf:resource="http://purl.obolibrary.org/obo/GO_0008150"/>
    <rdfs:subClassOf>
      <owl:Restriction>
        <owl:onProperty rdf:resource="http://example.org/unknown_relation"/>
        <owl:someValuesFrom rdf:resource="http://example.org/ExternalClass"/>
      </owl:Restriction>
    </rdfs:subClassOf>
  </owl:Class>
  <owl:Class rdf:about="http://example.org/ExternalClass"/>
""",
    )

    context = OwlLoader().load_context(
        path,
        expected_sha256=_checksum(path),
        source_url="http://purl.obolibrary.org/obo/go.owl",
        retrieval_date="2026-09-18",
        strict=False,
    )

    assert context.metadata.ontology_iri == "http://purl.obolibrary.org/obo/go.owl"
    assert context.metadata.release_date == "2025-02-06"
    assert context.metadata.declared_imports == (
        "http://example.org/pinned-import.owl",
    )
    assert context.metadata.import_definition_status == (
        ("http://example.org/pinned-import.owl", False),
    )
    assert context.complete_graph.triple_count > 0
    assert "http://example.org/ExternalClass" in context.complete_graph.class_iris
    unknown = next(
        edge
        for edge in context.edges
        if edge.relation_iri == "http://example.org/unknown_relation"
    )
    assert unknown.disposition is EdgeDispositionKind.UNKNOWN_OR_UNSUPPORTED
    assert unknown.parent_kind == "external"
    assert context.fingerprint.source_sha256 == _checksum(path)


def test_deprecated_stub_that_is_also_an_alternate_id_is_not_ambiguous(tmp_path):
    path = _write_owl(
        tmp_path,
        """
  <owl:Class rdf:about="http://purl.obolibrary.org/obo/GO_0008150">
    <oboInOwl:hasOBONamespace>biological_process</oboInOwl:hasOBONamespace>
  </owl:Class>
  <owl:Class rdf:about="http://purl.obolibrary.org/obo/GO_0000001">
    <rdfs:label>obsolete intermediate</rdfs:label>
    <oboInOwl:hasOBONamespace>biological_process</oboInOwl:hasOBONamespace>
    <oboInOwl:hasAlternativeId>GO:0000002</oboInOwl:hasAlternativeId>
    <owl:deprecated>true</owl:deprecated>
    <obo:IAO_0100001 rdf:resource="http://purl.obolibrary.org/obo/GO_0000003"/>
  </owl:Class>
  <owl:Class rdf:about="http://purl.obolibrary.org/obo/GO_0000002">
    <owl:deprecated>true</owl:deprecated>
    <obo:IAO_0100001 rdf:resource="http://purl.obolibrary.org/obo/GO_0000001"/>
  </owl:Class>
  <owl:Class rdf:about="http://purl.obolibrary.org/obo/GO_0000003">
    <oboInOwl:hasOBONamespace>biological_process</oboInOwl:hasOBONamespace>
    <rdfs:subClassOf rdf:resource="http://purl.obolibrary.org/obo/GO_0008150"/>
  </owl:Class>
""",
    )

    context = OwlLoader().load_context(
        path,
        expected_sha256=_checksum(path),
        source_url="http://purl.obolibrary.org/obo/go.owl",
        retrieval_date="2026-09-18",
        strict=False,
    )

    resolution = context.ontology.resolve("GO:0000002")
    assert resolution.status is TermStatus.REPLACED
    assert resolution.canonical_id == "GO:0000003"
    assert not any(
        issue.code == "AMBIGUOUS_ALTERNATE_ID" for issue in context.validation.issues
    )


def test_active_primary_collision_remains_an_auditable_ambiguity(tmp_path):
    path = _write_owl(
        tmp_path,
        """
  <owl:Class rdf:about="http://purl.obolibrary.org/obo/GO_0000001">
    <oboInOwl:hasOBONamespace>biological_process</oboInOwl:hasOBONamespace>
    <oboInOwl:hasAlternativeId>GO:0000002</oboInOwl:hasAlternativeId>
  </owl:Class>
  <owl:Class rdf:about="http://purl.obolibrary.org/obo/GO_0000002">
    <oboInOwl:hasOBONamespace>biological_process</oboInOwl:hasOBONamespace>
  </owl:Class>
""",
    )

    context = OwlLoader().load_context(
        path,
        expected_sha256=_checksum(path),
        source_url="http://purl.obolibrary.org/obo/go.owl",
        retrieval_date="2026-09-18",
        strict=False,
    )

    assert context.ontology.resolve("GO:0000002").canonical_id is None
    assert any(
        issue.code == "AMBIGUOUS_ALTERNATE_ID" for issue in context.validation.errors
    )


def test_subproperty_metadata_does_not_grant_propagation(tmp_path):
    path = _write_owl(
        tmp_path,
        """
  <owl:ObjectProperty rdf:about="http://example.org/narrow_part_of">
    <rdfs:subPropertyOf rdf:resource="http://purl.obolibrary.org/obo/BFO_0000050"/>
  </owl:ObjectProperty>
  <owl:Class rdf:about="http://purl.obolibrary.org/obo/GO_0008150">
    <oboInOwl:hasOBONamespace>biological_process</oboInOwl:hasOBONamespace>
  </owl:Class>
  <owl:Class rdf:about="http://purl.obolibrary.org/obo/GO_0000001">
    <oboInOwl:hasOBONamespace>biological_process</oboInOwl:hasOBONamespace>
    <rdfs:subClassOf>
      <owl:Restriction>
        <owl:onProperty rdf:resource="http://example.org/narrow_part_of"/>
        <owl:someValuesFrom rdf:resource="http://purl.obolibrary.org/obo/GO_0008150"/>
      </owl:Restriction>
    </rdfs:subClassOf>
  </owl:Class>
""",
    )

    first = OwlLoader().load_context(
        path,
        expected_sha256=_checksum(path),
        source_url="http://purl.obolibrary.org/obo/go.owl",
        retrieval_date="2026-09-18",
        strict=False,
    )
    second = OwlLoader().load_context(
        path,
        expected_sha256=_checksum(path),
        source_url="http://purl.obolibrary.org/obo/go.owl",
        retrieval_date="2026-09-18",
        strict=False,
    )

    edge = next(
        edge
        for edge in first.edges
        if edge.relation_iri == "http://example.org/narrow_part_of"
    )
    descriptor = next(
        descriptor
        for descriptor in first.relations
        if descriptor.iri == "http://example.org/narrow_part_of"
    )
    assert edge.axiom_origin is AxiomOrigin.ASSERTED_RESTRICTION
    assert edge.disposition is EdgeDispositionKind.UNKNOWN_OR_UNSUPPORTED
    assert descriptor.direct_superproperties == (
        "http://purl.obolibrary.org/obo/BFO_0000050",
    )
    assert first.fingerprint == second.fingerprint
    assert first.edges == second.edges
