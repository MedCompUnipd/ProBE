"""Load the GO OWL/RDF serialization into :class:`GeneOntology`."""

from __future__ import annotations

from pathlib import Path

from rdflib import BNode, Graph, Literal, Namespace, URIRef
from rdflib.namespace import OWL, RDF, RDFS

from probe.ontology import GeneOntology, OntologyTerm
from probe.source import Source
from probe.validation import ValidationReport

OBO = Namespace("http://purl.obolibrary.org/obo/")
OBO_IN_OWL = Namespace("http://www.geneontology.org/formats/oboInOwl#")
IAO_REPLACED_BY = OBO["IAO_0100001"]
RELATION_NAMES = {
    "BFO:0000050": "part_of",
}


def _obo_id(value: object) -> str | None:
    if isinstance(value, Literal):
        text = str(value)
        return text if ":" in text else None
    if not isinstance(value, URIRef):
        return None
    tail = str(value).rsplit("/", maxsplit=1)[-1]
    if "_" not in tail:
        return None
    prefix, identifier = tail.split("_", maxsplit=1)
    return f"{prefix}:{identifier}"


def _relation_name(value: object) -> str | None:
    relation_id = _obo_id(value)
    if relation_id is None:
        return None
    return RELATION_NAMES.get(relation_id, relation_id)


class OwlLoader:
    """Decode the subset of GO OWL needed for annotation comparison."""

    def load(
        self,
        source: Source | str | Path,
        *,
        report: ValidationReport | None = None,
        strict: bool = True,
    ) -> GeneOntology:
        source = Source.from_value(source)
        report = report if report is not None else ValidationReport()
        graph = Graph()
        try:
            with source.open_binary() as handle:
                graph.parse(file=handle, format="xml")
        except Exception as error:
            report.error(
                "INVALID_OWL",
                f"could not parse OWL/RDF: {error}",
                source=source.name,
            )
            if strict:
                report.raise_for_errors()
            return GeneOntology((), source=source)

        term_nodes: dict[str, URIRef] = {}
        for node in graph.subjects(RDF.type, OWL.Class):
            if (
                isinstance(node, URIRef)
                and (identifier := _obo_id(node))
                and identifier.startswith("GO:")
            ):
                term_nodes[identifier] = node

        terms: list[OntologyTerm] = []
        edges: list[tuple[str, str, str]] = []
        for identifier, node in term_nodes.items():
            label = str(graph.value(node, RDFS.label) or "")
            namespace = str(graph.value(node, OBO_IN_OWL.hasOBONamespace) or "")
            deprecated = graph.value(node, OWL.deprecated)
            obsolete = str(deprecated).lower() == "true"
            alternate_ids = tuple(
                str(value) for value in graph.objects(node, OBO_IN_OWL.hasAlternativeId)
            )
            replaced_by = tuple(
                replacement
                for value in graph.objects(node, IAO_REPLACED_BY)
                if (replacement := _obo_id(value))
            )
            consider = tuple(
                replacement
                for value in graph.objects(node, OBO_IN_OWL.consider)
                if (replacement := _obo_id(value))
            )
            terms.append(
                OntologyTerm(
                    identifier=identifier,
                    label=label,
                    namespace=namespace,
                    obsolete=obsolete,
                    alternate_ids=alternate_ids,
                    replaced_by=replaced_by,
                    consider=consider,
                )
            )

            for parent_node in graph.objects(node, RDFS.subClassOf):
                if (
                    isinstance(parent_node, URIRef)
                    and (parent_id := _obo_id(parent_node))
                    and parent_id.startswith("GO:")
                ):
                    edges.append((identifier, "is_a", parent_id))
                elif isinstance(parent_node, BNode):
                    property_node = graph.value(parent_node, OWL.onProperty)
                    target_node = graph.value(parent_node, OWL.someValuesFrom)
                    relation = _relation_name(property_node)
                    target = _obo_id(target_node)
                    if relation and target and target.startswith("GO:"):
                        edges.append((identifier, relation, target))

        version_iri = next(graph.objects(None, OWL.versionIRI), None)
        ontology = GeneOntology(
            terms,
            edges,
            release=str(version_iri) if version_iri else None,
            source=source,
        )
        report.extend(ontology.validate())
        if not ontology:
            report.error(
                "EMPTY_GO_ONTOLOGY",
                "OWL contains no GO classes",
                source=source.name,
            )
        if strict:
            report.raise_for_errors()
        return ontology
