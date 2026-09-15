"""Load the GO OWL/RDF serialization into :class:`GeneOntology`."""

from __future__ import annotations

from pathlib import Path

from rdflib import BNode, Graph, Literal, Namespace, URIRef
from rdflib.namespace import OWL, RDF, RDFS

from probe.ontology import (
    HAS_PART,
    NEGATIVELY_REGULATES,
    PART_OF,
    POSITIVELY_REGULATES,
    REGULATES,
    GeneOntology,
    OntologyTerm,
)
from probe.source import Source
from probe.validation import ValidationReport

OBO = Namespace("http://purl.obolibrary.org/obo/")
OBO_IN_OWL = Namespace("http://www.geneontology.org/formats/oboInOwl#")
IAO_REPLACED_BY = OBO["IAO_0100001"]
RELATION_IRIS = {
    str(OBO["BFO_0000050"]): PART_OF,
    str(OBO["BFO_0000051"]): HAS_PART,
    str(OBO["BFO_0000066"]): "occurs_in",
    str(OBO["RO_0001025"]): "located_in",
    str(OBO["RO_0002211"]): REGULATES,
    str(OBO["RO_0002212"]): NEGATIVELY_REGULATES,
    str(OBO["RO_0002213"]): POSITIVELY_REGULATES,
    str(OBO["RO_0002215"]): "capable_of",
    str(OBO["RO_0002216"]): "capable_of_part_of",
    str(OBO["RO_0002327"]): "enables",
    str(OBO["RO_0002331"]): "involved_in",
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
    if not isinstance(value, URIRef):
        return None
    return RELATION_IRIS.get(str(value), _obo_id(value) or str(value))


def _rdf_list(graph: Graph, head: object) -> tuple[object, ...]:
    members: list[object] = []
    visited: set[object] = set()
    current = head
    while current != RDF.nil and current not in visited:
        visited.add(current)
        member = graph.value(current, RDF.first)
        if member is None:
            break
        members.append(member)
        current = graph.value(current, RDF.rest)
        if current is None:
            break
    return tuple(members)


def _expression_edges(
    graph: Graph,
    expression: object,
    *,
    visited: set[object] | None = None,
) -> tuple[tuple[str, str], ...]:
    """Extract GO superclass and existential-restriction edges."""

    if isinstance(expression, URIRef):
        target = _obo_id(expression)
        return (("is_a", target),) if target and target.startswith("GO:") else ()
    if not isinstance(expression, BNode):
        return ()

    visited = visited if visited is not None else set()
    if expression in visited:
        return ()
    visited.add(expression)

    property_node = graph.value(expression, OWL.onProperty)
    target_node = graph.value(expression, OWL.someValuesFrom)
    relation = _relation_name(property_node)
    target = _obo_id(target_node)
    if relation and target and target.startswith("GO:"):
        return ((relation, target),)

    edges: list[tuple[str, str]] = []
    for list_head in graph.objects(expression, OWL.intersectionOf):
        for member in _rdf_list(graph, list_head):
            edges.extend(_expression_edges(graph, member, visited=visited))
    return tuple(edges)


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
            deprecated_value = graph.value(node, OWL.deprecated)
            deprecated = str(deprecated_value).lower() == "true"
            obsolete = label.casefold().startswith("obsolete ")
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
                    deprecated=deprecated,
                    alternate_ids=alternate_ids,
                    replaced_by=replaced_by,
                    consider=consider,
                )
            )

            expressions = (
                *graph.objects(node, RDFS.subClassOf),
                *graph.objects(node, OWL.equivalentClass),
            )
            for expression in expressions:
                for relation, target in _expression_edges(graph, expression):
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
