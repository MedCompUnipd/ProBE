"""IRI-based ontology relation policy and auditable edge dispositions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum

from rdflib.namespace import RDFS

IS_A_IRI = str(RDFS.subClassOf)
PART_OF_IRI = "http://purl.obolibrary.org/obo/BFO_0000050"
HAS_PART_IRI = "http://purl.obolibrary.org/obo/BFO_0000051"
OCCURS_IN_IRI = "http://purl.obolibrary.org/obo/BFO_0000066"
LOCATED_IN_IRI = "http://purl.obolibrary.org/obo/RO_0001025"
REGULATES_IRI = "http://purl.obolibrary.org/obo/RO_0002211"
NEGATIVELY_REGULATES_IRI = "http://purl.obolibrary.org/obo/RO_0002212"
POSITIVELY_REGULATES_IRI = "http://purl.obolibrary.org/obo/RO_0002213"
CAPABLE_OF_IRI = "http://purl.obolibrary.org/obo/RO_0002215"
CAPABLE_OF_PART_OF_IRI = "http://purl.obolibrary.org/obo/RO_0002216"
ENABLES_IRI = "http://purl.obolibrary.org/obo/RO_0002327"
INVOLVED_IN_IRI = "http://purl.obolibrary.org/obo/RO_0002331"

RELATION_NAMES = {
    IS_A_IRI: "is_a",
    PART_OF_IRI: "part_of",
    HAS_PART_IRI: "has_part",
    OCCURS_IN_IRI: "occurs_in",
    LOCATED_IN_IRI: "located_in",
    REGULATES_IRI: "regulates",
    NEGATIVELY_REGULATES_IRI: "negatively_regulates",
    POSITIVELY_REGULATES_IRI: "positively_regulates",
    CAPABLE_OF_IRI: "capable_of",
    CAPABLE_OF_PART_OF_IRI: "capable_of_part_of",
    ENABLES_IRI: "enables",
    INVOLVED_IN_IRI: "involved_in",
}


class GOAspect(StrEnum):
    MF = "F"
    BP = "P"
    CC = "C"


NAMESPACE_ASPECTS = {
    "molecular_function": GOAspect.MF,
    "biological_process": GOAspect.BP,
    "cellular_component": GOAspect.CC,
}


class EdgeDispositionKind(StrEnum):
    PROPAGATING = "propagating"
    NAVIGABLE_ONLY = "navigable_only"
    EXTERNAL_CONTEXT = "external_context"
    CROSS_ASPECT = "cross_aspect"
    UNKNOWN_OR_UNSUPPORTED = "unknown_or_unsupported"


class AxiomOrigin(StrEnum):
    ASSERTED_NAMED_SUBCLASS = "asserted_named_subclass"
    ASSERTED_RESTRICTION = "asserted_restriction"
    ASSERTED_EQUIVALENT_CLASS = "asserted_equivalent_class"
    RECONSTRUCTED_INTERSECTION_MEMBER = "reconstructed_intersection_member"
    INFERRED_BY_PINNED_REASONER = "inferred_by_pinned_reasoner"


@dataclass(frozen=True, slots=True)
class PolicyIdentity:
    name: str
    version: str
    canonical_configuration: str
    fingerprint: str

    @classmethod
    def from_configuration(
        cls,
        name: str,
        version: str,
        configuration: object,
    ) -> PolicyIdentity:
        canonical = json.dumps(
            configuration,
            sort_keys=True,
            separators=(",", ":"),
        )
        fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return cls(name, version, canonical, fingerprint)


@dataclass(frozen=True, slots=True)
class ExtractedOntologyEdge:
    edge_id: str
    child_iri: str
    parent_iri: str
    child_kind: str
    parent_kind: str
    child_aspect: GOAspect | None
    parent_aspect: GOAspect | None
    relation_iri: str
    axiom_origin: AxiomOrigin
    disposition: EdgeDispositionKind
    disposition_reason: str
    relation_policy_fingerprint: str


@dataclass(frozen=True, slots=True)
class RelationExample:
    child_iri: str
    parent_iri: str
    disposition: EdgeDispositionKind


@dataclass(frozen=True, slots=True)
class RelationDescriptor:
    iri: str
    compact_id: str | None
    labels: tuple[str, ...]
    direct_superproperties: tuple[str, ...]
    all_superproperties: tuple[str, ...]
    inverse_iri: str | None
    transitive: bool
    symmetric: bool
    domains: tuple[str, ...]
    ranges: tuple[str, ...]
    total_edge_count: int
    go_to_go_edge_count: int
    go_to_external_edge_count: int
    aspect_pairs: tuple[tuple[str, str], ...]
    observed_directions: tuple[str, ...]
    disposition_counts: tuple[tuple[EdgeDispositionKind, int], ...]
    examples: tuple[RelationExample, ...]


@dataclass(frozen=True, slots=True)
class RelationPolicy:
    """Exact-IRI permissions; metadata never grants propagation implicitly."""

    identity: PolicyIdentity
    propagating_iris: frozenset[str]
    navigable_iris: frozenset[str]
    cross_aspect_rule: str = "exclude"
    external_endpoint_rule: str = "retain_context_only"

    @classmethod
    def conservative(cls) -> RelationPolicy:
        propagating = frozenset({IS_A_IRI, PART_OF_IRI})
        navigable = frozenset(
            {
                *propagating,
                HAS_PART_IRI,
                OCCURS_IN_IRI,
                LOCATED_IN_IRI,
                REGULATES_IRI,
                NEGATIVELY_REGULATES_IRI,
                POSITIVELY_REGULATES_IRI,
                CAPABLE_OF_IRI,
                CAPABLE_OF_PART_OF_IRI,
                ENABLES_IRI,
                INVOLVED_IN_IRI,
            }
        )
        configuration = {
            "propagating_iris": sorted(propagating),
            "navigable_iris": sorted(navigable),
            "subproperty_inheritance": [],
            "cross_aspect_rule": "exclude",
            "external_endpoint_rule": "retain_context_only",
        }
        return cls(
            identity=PolicyIdentity.from_configuration(
                "conservative_go_annotation_relations",
                "1",
                configuration,
            ),
            propagating_iris=propagating,
            navigable_iris=navigable,
        )

    def classify_edge(
        self,
        *,
        child_iri: str,
        parent_iri: str,
        child_kind: str,
        parent_kind: str,
        child_aspect: GOAspect | None,
        parent_aspect: GOAspect | None,
        relation_iri: str,
        axiom_origin: AxiomOrigin,
    ) -> ExtractedOntologyEdge:
        if axiom_origin is AxiomOrigin.INFERRED_BY_PINNED_REASONER:
            disposition = EdgeDispositionKind.UNKNOWN_OR_UNSUPPORTED
            reason = "reasoner-derived edges are not approved in this axiom mode"
        elif relation_iri not in self.navigable_iris:
            disposition = EdgeDispositionKind.UNKNOWN_OR_UNSUPPORTED
            reason = "relation IRI has no approved use in this policy"
        elif child_kind == "external" or parent_kind == "external":
            disposition = EdgeDispositionKind.EXTERNAL_CONTEXT
            reason = "one or both class endpoints are outside GO"
        elif child_kind != "active_go" or parent_kind != "active_go":
            disposition = EdgeDispositionKind.UNKNOWN_OR_UNSUPPORTED
            reason = "one or both GO endpoints are obsolete, deprecated, or invalid"
        elif child_aspect is None or parent_aspect is None:
            disposition = EdgeDispositionKind.UNKNOWN_OR_UNSUPPORTED
            reason = "one or both GO endpoints have no recognized aspect"
        elif child_aspect is not parent_aspect:
            disposition = EdgeDispositionKind.CROSS_ASPECT
            reason = "GO endpoints belong to different aspects"
        elif relation_iri in self.propagating_iris:
            disposition = EdgeDispositionKind.PROPAGATING
            reason = "exact relation IRI and active same-aspect endpoints are approved"
        else:
            disposition = EdgeDispositionKind.NAVIGABLE_ONLY
            reason = "relation is retained for explicit navigation but not inheritance"

        edge_key = "\x1f".join(
            (
                child_iri,
                relation_iri,
                parent_iri,
                axiom_origin.value,
                disposition.value,
            )
        )
        return ExtractedOntologyEdge(
            edge_id=hashlib.sha256(edge_key.encode("utf-8")).hexdigest(),
            child_iri=child_iri,
            parent_iri=parent_iri,
            child_kind=child_kind,
            parent_kind=parent_kind,
            child_aspect=child_aspect,
            parent_aspect=parent_aspect,
            relation_iri=relation_iri,
            axiom_origin=axiom_origin,
            disposition=disposition,
            disposition_reason=reason,
            relation_policy_fingerprint=self.identity.fingerprint,
        )


def compact_iri(iri: str) -> str | None:
    if iri == IS_A_IRI:
        return "rdfs:subClassOf"
    prefix = "http://purl.obolibrary.org/obo/"
    if not iri.startswith(prefix):
        return None
    tail = iri.removeprefix(prefix)
    if "_" not in tail:
        return None
    namespace, identifier = tail.split("_", maxsplit=1)
    return f"{namespace}:{identifier}"


def operational_relation_name(iri: str) -> str:
    return RELATION_NAMES.get(iri, compact_iri(iri) or iri)
