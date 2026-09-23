"""Load one pinned OWL artifact and derive an auditable GO projection."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import rdflib
from rdflib import BNode, Graph, Literal, Namespace, URIRef
from rdflib.namespace import OWL, RDF, RDFS

from probe.ontology import GeneOntology, OntologyTerm
from probe.relations import (
    IS_A_IRI,
    NAMESPACE_ASPECTS,
    AxiomOrigin,
    EdgeDispositionKind,
    ExtractedOntologyEdge,
    PolicyIdentity,
    RelationDescriptor,
    RelationExample,
    RelationPolicy,
    compact_iri,
    operational_relation_name,
)
from probe.source import Source
from probe.validation import ValidationReport

OBO = Namespace("http://purl.obolibrary.org/obo/")
OBO_IN_OWL = Namespace("http://www.geneontology.org/formats/oboInOwl#")
IAO_REPLACED_BY = OBO["IAO_0100001"]
AXIOM_MODE = "asserted_and_locally_reconstructible_v1"
IMPORT_POLICY_NAME = "declaration_only_no_network"
PARSER_CONFIGURATION = {
    "format": "xml",
    "imports": "declarations_only_no_network",
    "reasoner": None,
    "supported_restrictions": ["owl:someValuesFrom"],
    "supported_reconstruction": ["owl:intersectionOf"],
}


@dataclass(frozen=True, slots=True)
class CompleteOntologyGraph:
    """Read-only query facade over every parsed triple in the supplied file."""

    _graph: Graph = field(repr=False, compare=False)
    class_iris: tuple[str, ...]
    object_property_iris: tuple[str, ...]

    @property
    def triple_count(self) -> int:
        return len(self._graph)

    def triples(self, pattern: tuple[object | None, object | None, object | None]):
        return self._graph.triples(pattern)

    def objects(self, subject: object, predicate: object) -> Iterator[object]:
        return self._graph.objects(subject, predicate)

    def subjects(self, predicate: object, obj: object) -> Iterator[object]:
        return self._graph.subjects(predicate, obj)


@dataclass(frozen=True, slots=True)
class OntologyFingerprint:
    value: str
    source_sha256: str
    ontology_iri: str
    version_iri: str
    parser_name: str
    parser_version: str
    parser_configuration_fingerprint: str
    import_closure_fingerprint: str
    axiom_mode: str


@dataclass(frozen=True, slots=True)
class OntologyMetadata:
    source_url: str
    local_source_path: str
    retrieval_date: str
    ontology_iri: str
    version_iri: str
    release_date: str
    declared_imports: tuple[str, ...]
    import_definition_status: tuple[tuple[str, bool], ...]
    rdflib_version: str
    fingerprint: OntologyFingerprint
    import_policy: PolicyIdentity


@dataclass(frozen=True, slots=True)
class OntologyContext:
    ontology: GeneOntology
    complete_graph: CompleteOntologyGraph
    metadata: OntologyMetadata
    fingerprint: OntologyFingerprint
    relation_policy: RelationPolicy
    edges: tuple[ExtractedOntologyEdge, ...]
    relations: tuple[RelationDescriptor, ...]
    unsupported_axiom_counts: tuple[tuple[str, int], ...]
    validation: ValidationReport = field(compare=False)


@dataclass(frozen=True, slots=True)
class _RawEdge:
    child_iri: str
    parent_iri: str
    relation_iri: str
    origin: AxiomOrigin


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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


def _go_id(value: object) -> str | None:
    identifier = _obo_id(value)
    return identifier if identifier and identifier.startswith("GO:") else None


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
    child: URIRef,
    expression: object,
    *,
    origin: AxiomOrigin,
    unsupported: Counter[str],
    visited: frozenset[object] = frozenset(),
) -> tuple[_RawEdge, ...]:
    if isinstance(expression, URIRef):
        return (_RawEdge(str(child), str(expression), IS_A_IRI, origin),)
    if not isinstance(expression, BNode):
        unsupported["non_resource_class_expression"] += 1
        return ()
    if expression in visited:
        unsupported["cyclic_anonymous_expression"] += 1
        return ()

    next_visited = visited | {expression}
    property_node = graph.value(expression, OWL.onProperty)
    target_node = graph.value(expression, OWL.someValuesFrom)
    if isinstance(property_node, URIRef) and isinstance(target_node, URIRef):
        return (
            _RawEdge(
                str(child),
                str(target_node),
                str(property_node),
                origin,
            ),
        )
    if property_node is not None:
        unsupported["unsupported_restriction"] += 1
        return ()

    edges: list[_RawEdge] = []
    list_heads = tuple(graph.objects(expression, OWL.intersectionOf))
    if list_heads:
        for list_head in list_heads:
            for member in _rdf_list(graph, list_head):
                edges.extend(
                    _expression_edges(
                        graph,
                        child,
                        member,
                        origin=AxiomOrigin.RECONSTRUCTED_INTERSECTION_MEMBER,
                        unsupported=unsupported,
                        visited=next_visited,
                    )
                )
        return tuple(edges)

    unsupported["unsupported_anonymous_expression"] += 1
    return ()


def _all_superproperties(
    iri: str,
    direct: dict[str, tuple[str, ...]],
    visited: frozenset[str] = frozenset(),
) -> frozenset[str]:
    if iri in visited:
        return frozenset()
    result: set[str] = set()
    for parent in direct.get(iri, ()):
        result.add(parent)
        result.update(_all_superproperties(parent, direct, visited | {iri}))
    result.discard(iri)
    return frozenset(result)


def _term_kind(term: OntologyTerm | None) -> str:
    if term is None:
        return "external"
    if term.is_active and term.namespace in NAMESPACE_ASPECTS:
        return "active_go"
    return "inactive_go"


def _extract_terms(graph: Graph) -> tuple[dict[str, OntologyTerm], dict[str, URIRef]]:
    nodes: dict[str, URIRef] = {}
    for node in graph.subjects(RDF.type, OWL.Class):
        if isinstance(node, URIRef) and (identifier := _go_id(node)):
            nodes[identifier] = node

    terms: dict[str, OntologyTerm] = {}
    for identifier, node in sorted(nodes.items()):
        label = str(graph.value(node, RDFS.label) or "")
        namespace = str(graph.value(node, OBO_IN_OWL.hasOBONamespace) or "")
        deprecated = str(graph.value(node, OWL.deprecated) or "").lower() == "true"
        obsolete = label.casefold().startswith("obsolete ")
        alternate_ids = tuple(
            sorted(
                str(value) for value in graph.objects(node, OBO_IN_OWL.hasAlternativeId)
            )
        )
        replaced_by = tuple(
            sorted(
                replacement
                for value in graph.objects(node, IAO_REPLACED_BY)
                if (replacement := _obo_id(value))
            )
        )
        consider = tuple(
            sorted(
                candidate
                for value in graph.objects(node, OBO_IN_OWL.consider)
                if (candidate := _obo_id(value))
            )
        )
        terms[identifier] = OntologyTerm(
            identifier=identifier,
            label=label,
            namespace=namespace,
            obsolete=obsolete,
            deprecated=deprecated,
            alternate_ids=alternate_ids,
            replaced_by=replaced_by,
            consider=consider,
        )
    return terms, nodes


def _extract_raw_edges(
    graph: Graph,
    term_nodes: dict[str, URIRef],
) -> tuple[tuple[_RawEdge, ...], tuple[tuple[str, int], ...]]:
    unsupported: Counter[str] = Counter()
    raw_edges: list[_RawEdge] = []
    for identifier in sorted(term_nodes):
        node = term_nodes[identifier]
        for expression in graph.objects(node, RDFS.subClassOf):
            raw_edges.extend(
                _expression_edges(
                    graph,
                    node,
                    expression,
                    origin=(
                        AxiomOrigin.ASSERTED_NAMED_SUBCLASS
                        if isinstance(expression, URIRef)
                        else AxiomOrigin.ASSERTED_RESTRICTION
                    ),
                    unsupported=unsupported,
                )
            )
        for expression in graph.objects(node, OWL.equivalentClass):
            raw_edges.extend(
                _expression_edges(
                    graph,
                    node,
                    expression,
                    origin=AxiomOrigin.ASSERTED_EQUIVALENT_CLASS,
                    unsupported=unsupported,
                )
            )
    unique_edges = tuple(
        sorted(
            set(raw_edges),
            key=lambda edge: (
                edge.child_iri,
                edge.relation_iri,
                edge.parent_iri,
                edge.origin.value,
            ),
        )
    )
    return unique_edges, tuple(sorted(unsupported.items()))


def _relation_descriptors(
    graph: Graph,
    edges: tuple[ExtractedOntologyEdge, ...],
) -> tuple[RelationDescriptor, ...]:
    declared = {
        str(node)
        for node in graph.subjects(RDF.type, OWL.ObjectProperty)
        if isinstance(node, URIRef)
    }
    iris = declared | {edge.relation_iri for edge in edges}
    direct_superproperties = {
        iri: tuple(
            sorted(
                str(value)
                for value in graph.objects(URIRef(iri), RDFS.subPropertyOf)
                if isinstance(value, URIRef)
            )
        )
        for iri in iris
    }
    by_relation: dict[str, list[ExtractedOntologyEdge]] = defaultdict(list)
    for edge in edges:
        by_relation[edge.relation_iri].append(edge)

    descriptors: list[RelationDescriptor] = []
    for iri in sorted(iris):
        relation_edges = sorted(by_relation.get(iri, ()), key=lambda edge: edge.edge_id)
        disposition_counts = Counter(edge.disposition for edge in relation_edges)
        aspects = {
            (edge.child_aspect.value, edge.parent_aspect.value)
            for edge in relation_edges
            if edge.child_aspect is not None and edge.parent_aspect is not None
        }
        inverse_values = sorted(
            str(value)
            for value in graph.objects(URIRef(iri), OWL.inverseOf)
            if isinstance(value, URIRef)
        )
        labels = tuple(
            sorted(str(value) for value in graph.objects(URIRef(iri), RDFS.label))
        )
        domains = tuple(
            sorted(str(value) for value in graph.objects(URIRef(iri), RDFS.domain))
        )
        ranges = tuple(
            sorted(str(value) for value in graph.objects(URIRef(iri), RDFS.range))
        )
        descriptors.append(
            RelationDescriptor(
                iri=iri,
                compact_id=compact_iri(iri),
                labels=labels,
                direct_superproperties=direct_superproperties.get(iri, ()),
                all_superproperties=tuple(
                    sorted(_all_superproperties(iri, direct_superproperties))
                ),
                inverse_iri=inverse_values[0] if inverse_values else None,
                transitive=(URIRef(iri), RDF.type, OWL.TransitiveProperty) in graph,
                symmetric=(URIRef(iri), RDF.type, OWL.SymmetricProperty) in graph,
                domains=domains,
                ranges=ranges,
                total_edge_count=len(relation_edges),
                go_to_go_edge_count=sum(
                    edge.child_kind != "external" and edge.parent_kind != "external"
                    for edge in relation_edges
                ),
                go_to_external_edge_count=sum(
                    edge.child_kind == "external" or edge.parent_kind == "external"
                    for edge in relation_edges
                ),
                aspect_pairs=tuple(sorted(aspects)),
                observed_directions=("child_to_parent",) if relation_edges else (),
                disposition_counts=tuple(
                    sorted(disposition_counts.items(), key=lambda item: item[0].value)
                ),
                examples=tuple(
                    RelationExample(
                        edge.child_iri,
                        edge.parent_iri,
                        edge.disposition,
                    )
                    for edge in relation_edges[:5]
                ),
            )
        )
    return tuple(descriptors)


class OwlLoader:
    """Decode OWL without implicit imports or reasoning."""

    def load_context(
        self,
        source: Source | str | Path,
        *,
        expected_sha256: str,
        source_url: str,
        retrieval_date: str,
        relation_policy: RelationPolicy | None = None,
        report: ValidationReport | None = None,
        strict: bool = True,
    ) -> OntologyContext:
        artifact = Source.from_value(source)
        validation = report if report is not None else ValidationReport()
        actual_sha256 = artifact.checksum("sha256")
        if actual_sha256.lower() != expected_sha256.lower():
            validation.error(
                "CHECKSUM_MISMATCH",
                f"expected {expected_sha256.lower()}, got {actual_sha256.lower()}",
                source=artifact.name,
            )
            validation.raise_for_errors()

        graph = Graph()
        try:
            with artifact.open_binary() as handle:
                graph.parse(file=handle, format="xml")
        except Exception as error:
            validation.error(
                "INVALID_OWL",
                f"could not parse OWL/RDF: {error}",
                source=artifact.name,
            )
            validation.raise_for_errors()

        ontology_nodes = sorted(
            str(node)
            for node in graph.subjects(RDF.type, OWL.Ontology)
            if isinstance(node, URIRef)
        )
        ontology_iri = ontology_nodes[0] if ontology_nodes else ""
        ontology_node = URIRef(ontology_iri) if ontology_iri else None
        version_values = (
            sorted(str(value) for value in graph.objects(ontology_node, OWL.versionIRI))
            if ontology_node is not None
            else []
        )
        version_iri = version_values[0] if version_values else ""
        declared_imports = (
            tuple(
                sorted(
                    str(value) for value in graph.objects(ontology_node, OWL.imports)
                )
            )
            if ontology_node is not None
            else ()
        )
        if not ontology_iri:
            validation.error(
                "MISSING_ONTOLOGY_IRI",
                "OWL contains no named owl:Ontology node",
                source=artifact.name,
            )
        if not version_iri:
            validation.error(
                "MISSING_VERSION_IRI",
                "ontology has no owl:versionIRI",
                source=artifact.name,
            )

        policy = relation_policy or RelationPolicy.conservative()
        terms, term_nodes = _extract_terms(graph)
        raw_edges, unsupported_axiom_counts = _extract_raw_edges(graph, term_nodes)
        iri_to_term = {
            str(node): terms[identifier] for identifier, node in term_nodes.items()
        }
        edges = tuple(
            sorted(
                (
                    policy.classify_edge(
                        child_iri=edge.child_iri,
                        parent_iri=edge.parent_iri,
                        child_kind=_term_kind(iri_to_term.get(edge.child_iri)),
                        parent_kind=_term_kind(iri_to_term.get(edge.parent_iri)),
                        child_aspect=(
                            NAMESPACE_ASPECTS.get(iri_to_term[edge.child_iri].namespace)
                            if edge.child_iri in iri_to_term
                            else None
                        ),
                        parent_aspect=(
                            NAMESPACE_ASPECTS.get(
                                iri_to_term[edge.parent_iri].namespace
                            )
                            if edge.parent_iri in iri_to_term
                            else None
                        ),
                        relation_iri=edge.relation_iri,
                        axiom_origin=edge.origin,
                    )
                    for edge in raw_edges
                ),
                key=lambda edge: edge.edge_id,
            )
        )

        projection_edges = []
        for edge in edges:
            if edge.disposition not in {
                EdgeDispositionKind.PROPAGATING,
                EdgeDispositionKind.NAVIGABLE_ONLY,
            }:
                continue
            child_id = _go_id(URIRef(edge.child_iri))
            parent_id = _go_id(URIRef(edge.parent_iri))
            if child_id and parent_id:
                projection_edges.append(
                    (
                        child_id,
                        operational_relation_name(edge.relation_iri),
                        parent_id,
                    )
                )
        ontology = GeneOntology(
            terms.values(),
            projection_edges,
            release=version_iri or None,
            source=artifact,
        )
        validation.extend(ontology.validate())
        if not ontology:
            validation.error(
                "EMPTY_GO_ONTOLOGY",
                "OWL contains no GO classes",
                source=artifact.name,
            )

        import_policy = PolicyIdentity.from_configuration(
            IMPORT_POLICY_NAME,
            "1",
            {"download_imports": False, "declared_imports": declared_imports},
        )
        parser_configuration_fingerprint = _sha256_text(
            _canonical_json(PARSER_CONFIGURATION)
        )
        import_closure_fingerprint = _sha256_text(_canonical_json(declared_imports))
        fingerprint_payload = {
            "source_sha256": actual_sha256,
            "ontology_iri": ontology_iri,
            "version_iri": version_iri,
            "parser_name": "rdflib",
            "parser_version": rdflib.__version__,
            "parser_configuration_fingerprint": parser_configuration_fingerprint,
            "import_closure_fingerprint": import_closure_fingerprint,
            "axiom_mode": AXIOM_MODE,
            "relation_policy_fingerprint": policy.identity.fingerprint,
        }
        fingerprint = OntologyFingerprint(
            value=_sha256_text(_canonical_json(fingerprint_payload)),
            source_sha256=actual_sha256,
            ontology_iri=ontology_iri,
            version_iri=version_iri,
            parser_name="rdflib",
            parser_version=rdflib.__version__,
            parser_configuration_fingerprint=parser_configuration_fingerprint,
            import_closure_fingerprint=import_closure_fingerprint,
            axiom_mode=AXIOM_MODE,
        )
        release_match = re.search(r"/releases/(\d{4}-\d{2}-\d{2})/", version_iri)
        release_date = release_match.group(1) if release_match else ""
        if not release_date:
            validation.warning(
                "MISSING_RELEASE_DATE",
                "release date could not be derived from ontology metadata",
                source=artifact.name,
            )

        class_iris = {
            str(node)
            for node in graph.subjects(RDF.type, OWL.Class)
            if isinstance(node, URIRef)
        }
        class_iris.update(edge.child_iri for edge in raw_edges)
        class_iris.update(edge.parent_iri for edge in raw_edges)
        property_iris = {
            str(node)
            for node in graph.subjects(RDF.type, OWL.ObjectProperty)
            if isinstance(node, URIRef)
        }
        property_iris.update(edge.relation_iri for edge in raw_edges)
        complete_graph = CompleteOntologyGraph(
            graph,
            tuple(sorted(class_iris)),
            tuple(sorted(property_iris)),
        )
        metadata = OntologyMetadata(
            source_url=source_url,
            local_source_path=artifact.name,
            retrieval_date=retrieval_date,
            ontology_iri=ontology_iri,
            version_iri=version_iri,
            release_date=release_date,
            declared_imports=declared_imports,
            import_definition_status=tuple(
                (import_iri, import_iri in ontology_nodes)
                for import_iri in declared_imports
            ),
            rdflib_version=rdflib.__version__,
            fingerprint=fingerprint,
            import_policy=import_policy,
        )
        context = OntologyContext(
            ontology=ontology,
            complete_graph=complete_graph,
            metadata=metadata,
            fingerprint=fingerprint,
            relation_policy=policy,
            edges=edges,
            relations=_relation_descriptors(graph, edges),
            unsupported_axiom_counts=unsupported_axiom_counts,
            validation=validation,
        )
        if strict:
            validation.raise_for_errors()
        return context

    def load(
        self,
        source: Source | str | Path,
        *,
        report: ValidationReport | None = None,
        strict: bool = True,
    ) -> GeneOntology:
        """Compatibility wrapper returning only the GO projection."""

        artifact = Source.from_value(source)
        validation = report if report is not None else ValidationReport()
        context = self.load_context(
            artifact,
            expected_sha256=artifact.checksum("sha256"),
            source_url=artifact.name,
            retrieval_date="",
            report=validation,
            strict=False,
        )
        if strict:
            validation.raise_for_errors()
        return context.ontology
