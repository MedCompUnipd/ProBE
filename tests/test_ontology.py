from __future__ import annotations

import math

from probe import GeneOntology, OntologyTerm, TermStatus


def test_ontology_resolves_alternate_ids_and_propagates():
    ontology = GeneOntology(
        [
            OntologyTerm("GO:0008150", namespace="biological_process"),
            OntologyTerm(
                "GO:0000001",
                namespace="biological_process",
                alternate_ids=("GO:9999999",),
            ),
            OntologyTerm("GO:0000002", namespace="biological_process"),
        ],
        [
            ("GO:0000001", "is_a", "GO:0008150"),
            ("GO:0000002", "part_of", "GO:0000001"),
        ],
    )

    assert ontology.resolve_id("GO:9999999") == "GO:0000001"
    assert ontology.propagate(["GO:0000002"]) == {
        "GO:0000002",
        "GO:0000001",
        "GO:0008150",
    }
    assert ontology.descendants("GO:0000001") == {"GO:0000002"}


def test_ontology_replaces_one_obsolete_id_but_not_consider_candidates():
    ontology = GeneOntology(
        [
            OntologyTerm("GO:0008150", namespace="biological_process"),
            OntologyTerm(
                "GO:0000001",
                label="obsolete old process",
                namespace="biological_process",
                obsolete=True,
                deprecated=True,
                replaced_by=("GO:0000002",),
            ),
            OntologyTerm("GO:0000002", namespace="biological_process"),
            OntologyTerm(
                "GO:0000003",
                label="obsolete ambiguous process",
                namespace="biological_process",
                obsolete=True,
                deprecated=True,
                consider=("GO:0000002", "GO:0008150"),
            ),
        ]
    )

    replaced = ontology.resolve("GO:0000001")
    ambiguous = ontology.resolve("GO:0000003")

    assert replaced.status is TermStatus.REPLACED
    assert replaced.canonical_id == "GO:0000002"
    assert ambiguous.status is TermStatus.OBSOLETE
    assert ambiguous.canonical_id is None
    assert ambiguous.candidates == ("GO:0000002", "GO:0008150")


def test_regulates_is_navigable_but_does_not_propagate_annotations():
    ontology = GeneOntology(
        [
            OntologyTerm("GO:0008150", namespace="biological_process"),
            OntologyTerm("GO:0000001", namespace="biological_process"),
            OntologyTerm("GO:0000002", namespace="biological_process"),
            OntologyTerm("GO:0000003", namespace="biological_process"),
        ],
        [
            ("GO:0000001", "is_a", "GO:0008150"),
            ("GO:0000002", "is_a", "GO:0008150"),
            ("GO:0000002", "regulates", "GO:0000001"),
            ("GO:0000003", "has_part", "GO:0000001"),
        ],
    )

    assert ontology.ancestors("GO:0000002") == {"GO:0000001", "GO:0008150"}
    assert ontology.propagate(["GO:0000002"]) == {"GO:0000002", "GO:0008150"}
    assert ontology.ancestors("GO:0000003") == set()


def test_not_constraint_excludes_node_and_safe_descendants():
    ontology = GeneOntology(
        [
            OntologyTerm("GO:0008150", namespace="biological_process"),
            OntologyTerm("GO:0000001", namespace="biological_process"),
            OntologyTerm("GO:0000002", namespace="biological_process"),
            OntologyTerm("GO:0000003", namespace="biological_process"),
        ],
        [
            ("GO:0000001", "is_a", "GO:0008150"),
            ("GO:0000002", "part_of", "GO:0000001"),
            ("GO:0000003", "regulates", "GO:0000001"),
        ],
    )

    assert ontology.excluded_by_not(["GO:0000001"]) == {
        "GO:0000001",
        "GO:0000002",
    }


def test_information_content_and_simgic_use_annotation_closure():
    ontology = GeneOntology(
        [
            OntologyTerm("GO:0008150", namespace="biological_process"),
            OntologyTerm("GO:0000001", namespace="biological_process"),
            OntologyTerm("GO:0000002", namespace="biological_process"),
            OntologyTerm("GO:0000003", namespace="biological_process"),
        ],
        [
            ("GO:0000001", "is_a", "GO:0008150"),
            ("GO:0000002", "is_a", "GO:0000001"),
            ("GO:0000003", "is_a", "GO:0008150"),
        ],
    )

    counts = ontology.cumulative_counts({"GO:0000002": 3, "GO:0000003": 1})
    information = ontology.information_content({"GO:0000002": 3, "GO:0000003": 1})

    assert counts["GO:0008150"] == 4
    assert counts["GO:0000001"] == 3
    assert information["GO:0008150"] == 0
    assert information["GO:0000001"] == -math.log(4 / 5)
    assert ontology.simgic(["GO:0000002"], ["GO:0000001"], information) == 0.5


def test_ontology_validation_rejects_propagation_cycles():
    ontology = GeneOntology(
        [OntologyTerm("GO:0000001"), OntologyTerm("GO:0000002")],
        [
            ("GO:0000001", "is_a", "GO:0000002"),
            ("GO:0000002", "part_of", "GO:0000001"),
        ],
    )

    report = ontology.validate()

    assert any(issue.code == "ONTOLOGY_CYCLE" for issue in report.errors)


def test_owl_loader_reads_go_classes_relations_and_replacements(tmp_path):
    path = tmp_path / "go.owl"
    path.write_text(
        """<?xml version="1.0"?>
<rdf:RDF
  xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
  xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"
  xmlns:owl="http://www.w3.org/2002/07/owl#"
  xmlns:obo="http://purl.obolibrary.org/obo/"
  xmlns:oboInOwl="http://www.geneontology.org/formats/oboInOwl#">
  <owl:Ontology rdf:about="http://purl.obolibrary.org/obo/go.owl">
    <owl:versionIRI rdf:resource="http://purl.obolibrary.org/obo/go/releases/2023-01-01/go.owl"/>
  </owl:Ontology>
  <owl:Class rdf:about="http://purl.obolibrary.org/obo/GO_0008150">
    <rdfs:label>biological_process</rdfs:label>
    <oboInOwl:hasOBONamespace>biological_process</oboInOwl:hasOBONamespace>
  </owl:Class>
  <owl:Class rdf:about="http://purl.obolibrary.org/obo/GO_0000001">
    <rdfs:label>example process</rdfs:label>
    <oboInOwl:hasOBONamespace>biological_process</oboInOwl:hasOBONamespace>
    <oboInOwl:hasAlternativeId>GO:9999999</oboInOwl:hasAlternativeId>
    <rdfs:subClassOf rdf:resource="http://purl.obolibrary.org/obo/GO_0008150"/>
  </owl:Class>
  <owl:Class rdf:about="http://purl.obolibrary.org/obo/GO_0000002">
    <rdfs:label>regulatory process</rdfs:label>
    <oboInOwl:hasOBONamespace>biological_process</oboInOwl:hasOBONamespace>
    <rdfs:subClassOf>
      <owl:Restriction>
        <owl:onProperty rdf:resource="http://purl.obolibrary.org/obo/RO_0002211"/>
        <owl:someValuesFrom rdf:resource="http://purl.obolibrary.org/obo/GO_0000001"/>
      </owl:Restriction>
    </rdfs:subClassOf>
  </owl:Class>
  <owl:Class rdf:about="http://purl.obolibrary.org/obo/GO_0000003">
    <rdfs:label>obsolete old process</rdfs:label>
    <oboInOwl:hasOBONamespace>biological_process</oboInOwl:hasOBONamespace>
    <owl:deprecated>true</owl:deprecated>
    <obo:IAO_0100001 rdf:resource="http://purl.obolibrary.org/obo/GO_0000001"/>
  </owl:Class>
</rdf:RDF>
""",
        encoding="utf-8",
    )

    ontology = GeneOntology.from_owl(path)

    assert len(ontology) == 4
    assert ontology.resolve_id("GO:9999999") == "GO:0000001"
    assert ontology.parents("GO:0000001") == {"GO:0008150"}
    assert ontology.parents("GO:0000002") == {"GO:0000001"}
    assert ontology.edges["GO:0000002"]["regulates"] == {"GO:0000001"}
    assert ontology.resolve("GO:0000003").status is TermStatus.REPLACED
    assert ontology.release and "2023-01-01" in ontology.release
