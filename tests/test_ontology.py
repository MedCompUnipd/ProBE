from __future__ import annotations

from probe import GeneOntology, OntologyTerm


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


def test_new_term_projects_to_nearest_old_ancestor():
    old = GeneOntology(
        [
            OntologyTerm("GO:0008150"),
            OntologyTerm("GO:0000001"),
        ],
        [("GO:0000001", "is_a", "GO:0008150")],
    )
    new = GeneOntology(
        [
            OntologyTerm("GO:0008150"),
            OntologyTerm("GO:0000001"),
            OntologyTerm("GO:0000002"),
        ],
        [
            ("GO:0000001", "is_a", "GO:0008150"),
            ("GO:0000002", "is_a", "GO:0000001"),
        ],
    )

    projection = old.project_from(new, "GO:0000002")

    assert projection.target_terms == {"GO:0000001"}
    assert projection.distance == 1
    assert projection.kind == "nearest_ancestor"


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


def test_owl_loader_reads_go_classes_and_edges(tmp_path):
    path = tmp_path / "go.owl"
    path.write_text(
        """<?xml version="1.0"?>
<rdf:RDF
  xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
  xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"
  xmlns:owl="http://www.w3.org/2002/07/owl#"
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
</rdf:RDF>
""",
        encoding="utf-8",
    )

    ontology = GeneOntology.from_owl(path)

    assert len(ontology) == 2
    assert ontology.resolve_id("GO:9999999") == "GO:0000001"
    assert ontology.parents("GO:0000001") == {"GO:0008150"}
    assert ontology.release and "2023-01-01" in ontology.release
