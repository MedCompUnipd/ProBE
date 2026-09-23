from __future__ import annotations

import hashlib

import pytest

from probe.masking import (
    EvaluationUniversePolicy,
    build_evaluation_universes,
    inclusive_ancestor_closure,
)
from probe.ontology_validation import OntologyPreflightPolicy, preflight_ontology
from probe.parsing.owl import OwlLoader
from probe.relations import IS_A_IRI, PART_OF_IRI, GOAspect
from probe.truth import DirectTruthTerm, SnapshotOrigin


def _context(tmp_path):
    path = tmp_path / "go.owl"
    path.write_text(
        """<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
 xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"
 xmlns:owl="http://www.w3.org/2002/07/owl#"
 xmlns:oboInOwl="http://www.geneontology.org/formats/oboInOwl#">
 <owl:Ontology rdf:about="http://purl.obolibrary.org/obo/go.owl">
  <owl:versionIRI rdf:resource="http://purl.obolibrary.org/obo/go/releases/2026-01-01/go.owl"/>
 </owl:Ontology>
 <owl:Class rdf:about="http://purl.obolibrary.org/obo/GO_0008150"><oboInOwl:hasOBONamespace>biological_process</oboInOwl:hasOBONamespace></owl:Class>
 <owl:Class rdf:about="http://purl.obolibrary.org/obo/GO_0003674"><oboInOwl:hasOBONamespace>molecular_function</oboInOwl:hasOBONamespace></owl:Class>
 <owl:Class rdf:about="http://purl.obolibrary.org/obo/GO_0005575"><oboInOwl:hasOBONamespace>cellular_component</oboInOwl:hasOBONamespace></owl:Class>
 <owl:Class rdf:about="http://purl.obolibrary.org/obo/GO_0000001">
  <oboInOwl:hasOBONamespace>biological_process</oboInOwl:hasOBONamespace>
  <rdfs:subClassOf rdf:resource="http://purl.obolibrary.org/obo/GO_0008150"/>
 </owl:Class>
 <owl:Class rdf:about="http://purl.obolibrary.org/obo/GO_0000002">
  <oboInOwl:hasOBONamespace>biological_process</oboInOwl:hasOBONamespace>
  <rdfs:subClassOf rdf:resource="http://purl.obolibrary.org/obo/GO_0000001"/>
 </owl:Class>
 <owl:Class rdf:about="http://purl.obolibrary.org/obo/GO_0000003">
  <oboInOwl:hasOBONamespace>biological_process</oboInOwl:hasOBONamespace>
  <rdfs:subClassOf>
   <owl:Restriction>
    <owl:onProperty rdf:resource="http://purl.obolibrary.org/obo/BFO_0000050"/>
    <owl:someValuesFrom rdf:resource="http://purl.obolibrary.org/obo/GO_0000001"/>
   </owl:Restriction>
  </rdfs:subClassOf>
 </owl:Class>
 <owl:Class rdf:about="http://purl.obolibrary.org/obo/GO_0000004">
  <oboInOwl:hasOBONamespace>biological_process</oboInOwl:hasOBONamespace>
  <rdfs:subClassOf rdf:resource="http://purl.obolibrary.org/obo/GO_0000002"/>
  <rdfs:subClassOf rdf:resource="http://purl.obolibrary.org/obo/GO_0000003"/>
 </owl:Class>
</rdf:RDF>
""",
        encoding="utf-8",
    )
    return OwlLoader().load_context(
        path,
        expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        source_url="http://purl.obolibrary.org/obo/go.owl",
        retrieval_date="2026-09-21",
        strict=False,
    )


def _direct(term_id: str) -> DirectTruthTerm:
    return DirectTruthTerm(
        sequence_id="sequence",
        aspect=GOAspect.BP,
        term_id=term_id,
        origin=SnapshotOrigin.T1,
        evidence_codes=frozenset({"IDA"}),
        selecting_event=None,
        source_assertions=(),
    )


def test_inclusive_closure_has_bounded_relation_provenance(tmp_path):
    context = _context(tmp_path)
    closure = inclusive_ancestor_closure([_direct("GO:0000003")], ontology=context)

    assert closure.terms == {"GO:0000003", "GO:0000001", "GO:0008150"}
    root_entry = next(
        item for item in closure.entries if item.propagated_term_id == "GO:0008150"
    )
    assert root_entry.minimum_distance == 2
    assert root_entry.relation_iris == {PART_OF_IRI, IS_A_IRI}
    assert tuple(step.target for step in root_entry.witness_path) == (
        "GO:0000001",
        "GO:0008150",
    )


def test_q_is_aspect_specific_root_free_and_policy_explicit(tmp_path):
    context = _context(tmp_path)
    preflight = preflight_ontology(
        context,
        policy=OntologyPreflightPolicy.synthetic_fixture(),
        strict=True,
    )
    is_a_only = build_evaluation_universes(
        context,
        approved_preflight=preflight,
        policy=EvaluationUniversePolicy.is_a_rooted(),
    )
    safe_paths = build_evaluation_universes(
        context,
        approved_preflight=preflight,
        policy=EvaluationUniversePolicy.safe_root_paths(),
    )

    assert is_a_only[GOAspect.BP].terms == {
        "GO:0000001",
        "GO:0000002",
        "GO:0000004",
    }
    assert "GO:0008150" not in is_a_only[GOAspect.BP].terms
    assert safe_paths[GOAspect.BP].terms == {
        "GO:0000001",
        "GO:0000002",
        "GO:0000003",
        "GO:0000004",
    }
    assert not is_a_only[GOAspect.MF].terms
    assert not is_a_only[GOAspect.CC].terms


def test_closure_preserves_all_sources_and_one_diamond_witness(tmp_path):
    context = _context(tmp_path)
    multiple = inclusive_ancestor_closure(
        [_direct("GO:0000002"), _direct("GO:0000003")],
        ontology=context,
    )
    assert dict(multiple.sources_by_term)["GO:0000001"] == (
        "GO:0000002",
        "GO:0000003",
    )
    assert (
        len(
            [
                entry
                for entry in multiple.entries
                if entry.propagated_term_id == "GO:0000001"
            ]
        )
        == 2
    )

    diamond = inclusive_ancestor_closure([_direct("GO:0000004")], ontology=context)
    shared = next(
        entry for entry in diamond.entries if entry.propagated_term_id == "GO:0000001"
    )
    assert shared.minimum_distance == 2
    assert shared.relation_iris == {IS_A_IRI, PART_OF_IRI}
    assert len(shared.witness_path) == 2


def test_closure_rejects_unmappable_and_cross_aspect_direct_terms(tmp_path):
    context = _context(tmp_path)
    with pytest.raises(ValueError, match="invalid or unmappable"):
        inclusive_ancestor_closure([_direct("GO:9999999")], ontology=context)
    wrong_aspect = DirectTruthTerm(
        sequence_id="sequence",
        aspect=GOAspect.MF,
        term_id="GO:0000001",
        origin=SnapshotOrigin.T1,
        evidence_codes=frozenset({"IDA"}),
        selecting_event=None,
        source_assertions=(),
    )
    with pytest.raises(ValueError, match="does not belong to aspect F"):
        inclusive_ancestor_closure([wrong_aspect], ontology=context)
