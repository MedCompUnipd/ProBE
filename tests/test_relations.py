from __future__ import annotations

from probe import (
    AxiomOrigin,
    EdgeDispositionKind,
    ExtractedOntologyEdge,
    GOAspect,
    RelationPolicy,
)


def _edge(
    *,
    relation: str,
    child_aspect: GOAspect | None,
    parent_aspect: GOAspect | None,
    child_kind: str = "active_go",
    parent_kind: str = "active_go",
) -> ExtractedOntologyEdge:
    return RelationPolicy.conservative().classify_edge(
        child_iri="http://purl.obolibrary.org/obo/GO_0000001",
        parent_iri="http://purl.obolibrary.org/obo/GO_0008150",
        child_kind=child_kind,
        parent_kind=parent_kind,
        child_aspect=child_aspect,
        parent_aspect=parent_aspect,
        relation_iri=relation,
        axiom_origin=AxiomOrigin.ASSERTED_RESTRICTION,
    )


def test_conservative_relation_policy_is_exact_iri_and_endpoint_aware():
    policy = RelationPolicy.conservative()
    part_of = "http://purl.obolibrary.org/obo/BFO_0000050"
    regulates = "http://purl.obolibrary.org/obo/RO_0002211"

    assert (
        _edge(
            relation=part_of,
            child_aspect=GOAspect.BP,
            parent_aspect=GOAspect.BP,
        ).disposition
        is EdgeDispositionKind.PROPAGATING
    )
    assert (
        _edge(
            relation=regulates,
            child_aspect=GOAspect.BP,
            parent_aspect=GOAspect.BP,
        ).disposition
        is EdgeDispositionKind.NAVIGABLE_ONLY
    )
    assert (
        _edge(
            relation=part_of,
            child_aspect=GOAspect.BP,
            parent_aspect=GOAspect.MF,
        ).disposition
        is EdgeDispositionKind.CROSS_ASPECT
    )
    assert (
        _edge(
            relation=part_of,
            child_aspect=GOAspect.BP,
            parent_aspect=None,
            parent_kind="external",
        ).disposition
        is EdgeDispositionKind.EXTERNAL_CONTEXT
    )
    assert (
        _edge(
            relation="http://example.org/part_of",
            child_aspect=GOAspect.BP,
            parent_aspect=GOAspect.BP,
        ).disposition
        is EdgeDispositionKind.UNKNOWN_OR_UNSUPPORTED
    )
    assert (
        policy.identity.fingerprint
        == RelationPolicy.conservative().identity.fingerprint
    )


def test_contextual_and_causal_relations_never_propagate_by_default():
    relations = {
        "http://purl.obolibrary.org/obo/BFO_0000051",
        "http://purl.obolibrary.org/obo/BFO_0000066",
        "http://purl.obolibrary.org/obo/RO_0001025",
        "http://purl.obolibrary.org/obo/RO_0002211",
        "http://purl.obolibrary.org/obo/RO_0002212",
        "http://purl.obolibrary.org/obo/RO_0002213",
        "http://purl.obolibrary.org/obo/RO_0002215",
        "http://purl.obolibrary.org/obo/RO_0002216",
        "http://purl.obolibrary.org/obo/RO_0002327",
        "http://purl.obolibrary.org/obo/RO_0002331",
    }

    assert {
        _edge(
            relation=relation,
            child_aspect=GOAspect.BP,
            parent_aspect=GOAspect.BP,
        ).disposition
        for relation in relations
    } == {EdgeDispositionKind.NAVIGABLE_ONLY}
