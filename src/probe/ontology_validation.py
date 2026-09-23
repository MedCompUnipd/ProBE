"""Deterministic preflight over one immutable ontology context."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from enum import StrEnum

from probe.ontology import GO_ROOT_BY_NAMESPACE
from probe.parsing.owl import OntologyContext
from probe.relations import (
    IS_A_IRI,
    NAMESPACE_ASPECTS,
    EdgeDispositionKind,
    GOAspect,
    PolicyIdentity,
    RelationDescriptor,
)
from probe.validation import Severity


class ReachabilityFinding(StrEnum):
    ROOT_TERM = "root_term"
    VALID_IS_A_ROOT_PATH = "valid_is_a_root_path"
    NO_IS_A_ROOT_PATH_BUT_SAFE_PART_OF_PATH = "no_is_a_root_path_but_safe_part_of_path"
    NO_SAFE_PATH_TO_ASPECT_ROOT = "no_safe_path_to_aspect_root"
    CROSS_ASPECT_EDGE_IGNORED = "cross_aspect_edge_ignored"
    NO_OWN_ASPECT_ROOT_AFTER_EDGE_FILTERING = "no_own_aspect_root_after_edge_filtering"
    EXTERNAL_CLASS = "external_class"
    OBSOLETE_OR_DEPRECATED = "obsolete_or_deprecated"


@dataclass(frozen=True, slots=True)
class OntologyPreflightPolicy:
    identity: PolicyIdentity
    require_go_owl: bool
    maximum_examples_per_issue: int = 5

    @classmethod
    def official_go(cls) -> OntologyPreflightPolicy:
        return cls(
            PolicyIdentity.from_configuration(
                "official_go_owl_preflight",
                "1",
                {
                    "require_go_owl": True,
                    "maximum_examples_per_issue": 5,
                    "part_of_only_root_path": "quarantine",
                },
            ),
            require_go_owl=True,
        )

    @classmethod
    def synthetic_fixture(cls) -> OntologyPreflightPolicy:
        return cls(
            PolicyIdentity.from_configuration(
                "synthetic_ontology_preflight",
                "1",
                {
                    "require_go_owl": False,
                    "maximum_examples_per_issue": 5,
                    "part_of_only_root_path": "quarantine",
                },
            ),
            require_go_owl=False,
        )


@dataclass(frozen=True, slots=True)
class TermReachability:
    term_id: str
    aspect: GOAspect
    findings: frozenset[ReachabilityFinding]
    has_direct_is_a_parent: bool


@dataclass(frozen=True, slots=True)
class CycleRecord:
    nodes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RelationCycleRecord:
    relation_iri: str
    cycle: CycleRecord


@dataclass(frozen=True, slots=True)
class OntologyValidationIssue:
    severity: Severity
    code: str
    message: str
    subject_iri: str | None = None
    relation_iri: str | None = None
    examples: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class OntologyPreflightReport:
    report_id: str
    ontology_fingerprint: str
    import_policy_fingerprint: str
    relation_policy_fingerprint: str
    preflight_policy_fingerprint: str
    active_terms_by_aspect: tuple[tuple[GOAspect, int], ...]
    obsolete_count: int
    deprecated_count: int
    external_class_count: int
    relations: tuple[RelationDescriptor, ...]
    edge_disposition_counts: tuple[tuple[EdgeDispositionKind, int], ...]
    reachability: tuple[TermReachability, ...]
    active_terms_without_direct_is_a_parent: tuple[str, ...]
    active_terms_without_is_a_root_path: tuple[str, ...]
    active_terms_without_safe_root_path: tuple[str, ...]
    terms_with_only_non_propagating_edges: tuple[str, ...]
    terms_linked_only_to_external_classes: tuple[str, ...]
    subproperty_cycles: tuple[CycleRecord, ...]
    extracted_relation_cycles: tuple[RelationCycleRecord, ...]
    semantic_navigation_cycles: tuple[CycleRecord, ...]
    scoring_hierarchy_cycles: tuple[CycleRecord, ...]
    unknown_relations: tuple[str, ...]
    unsupported_axiom_counts: tuple[tuple[str, int], ...]
    issues: tuple[OntologyValidationIssue, ...]

    @property
    def errors(self) -> tuple[OntologyValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity is Severity.ERROR)

    @property
    def warnings(self) -> tuple[OntologyValidationIssue, ...]:
        return tuple(
            issue for issue in self.issues if issue.severity is Severity.WARNING
        )

    @property
    def infos(self) -> tuple[OntologyValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity is Severity.INFO)

    @property
    def is_valid(self) -> bool:
        return not self.errors


class OntologyPreflightError(ValueError):
    def __init__(self, report: OntologyPreflightReport) -> None:
        self.report = report
        super().__init__(
            "\n".join(f"{issue.code}: {issue.message}" for issue in report.errors)
        )


def _go_id(iri: str) -> str | None:
    prefix = "http://purl.obolibrary.org/obo/GO_"
    return f"GO:{iri.removeprefix(prefix)}" if iri.startswith(prefix) else None


def _reachable(
    start: str,
    target: str,
    parents: dict[str, set[str]],
) -> bool:
    queue = deque(sorted(parents.get(start, ())))
    visited: set[str] = set()
    while queue:
        node = queue.popleft()
        if node == target:
            return True
        if node in visited:
            continue
        visited.add(node)
        queue.extend(sorted(set(parents.get(node, ())) - visited))
    return False


def _strongly_connected_cycles(
    nodes: set[str],
    adjacency: dict[str, set[str]],
) -> tuple[CycleRecord, ...]:
    """Return deterministic SCC records for the selected directed projection."""

    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[tuple[str, ...]] = []

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for related in sorted(adjacency.get(node, ())):
            if related not in indices:
                visit(related)
                lowlinks[node] = min(lowlinks[node], lowlinks[related])
            elif related in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[related])
        if lowlinks[node] != indices[node]:
            return
        component: list[str] = []
        while stack:
            related = stack.pop()
            on_stack.remove(related)
            component.append(related)
            if related == node:
                break
        ordered = tuple(sorted(component))
        if len(ordered) > 1 or (
            len(ordered) == 1 and ordered[0] in adjacency.get(ordered[0], set())
        ):
            components.append(ordered)

    for node in sorted(nodes):
        if node not in indices:
            visit(node)
    return tuple(CycleRecord(component) for component in sorted(components))


def _canonical_report_payload(
    *,
    ontology_fingerprint: str,
    preflight_policy_fingerprint: str,
    active_terms_by_aspect: tuple[tuple[GOAspect, int], ...],
    disposition_counts: tuple[tuple[EdgeDispositionKind, int], ...],
    reachability: tuple[TermReachability, ...],
    cycles: tuple[CycleRecord, ...],
    issues: tuple[OntologyValidationIssue, ...],
) -> str:
    value = {
        "ontology_fingerprint": ontology_fingerprint,
        "preflight_policy_fingerprint": preflight_policy_fingerprint,
        "active_terms_by_aspect": [
            [aspect.value, count] for aspect, count in active_terms_by_aspect
        ],
        "disposition_counts": [
            [disposition.value, count] for disposition, count in disposition_counts
        ],
        "reachability": [
            [
                item.term_id,
                item.aspect.value,
                sorted(finding.value for finding in item.findings),
                item.has_direct_is_a_parent,
            ]
            for item in reachability
        ],
        "scoring_cycles": [list(cycle.nodes) for cycle in cycles],
        "issues": [
            [
                issue.severity.value,
                issue.code,
                issue.message,
                issue.subject_iri,
                issue.relation_iri,
                list(issue.examples),
            ]
            for issue in issues
        ],
    }
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def preflight_ontology(
    context: OntologyContext,
    *,
    policy: OntologyPreflightPolicy | None = None,
    strict: bool = True,
) -> OntologyPreflightReport:
    active_policy = policy or OntologyPreflightPolicy.official_go()
    maximum_examples = active_policy.maximum_examples_per_issue
    ontology = context.ontology
    issues = [
        OntologyValidationIssue(
            issue.severity,
            issue.code,
            issue.message,
            examples=tuple(
                value
                for value in (
                    issue.source,
                    str(issue.line) if issue.line is not None else None,
                )
                if value is not None
            ),
        )
        for issue in context.validation.issues
    ]
    ontology_identity = (
        f"{context.metadata.ontology_iri} {context.metadata.version_iri}"
    ).casefold()
    if active_policy.require_go_owl and "go.owl" not in ontology_identity:
        issues.append(
            OntologyValidationIssue(
                Severity.ERROR,
                "NON_GO_ONTOLOGY",
                "definitive preflight requires an official go.owl ontology",
                examples=(
                    context.metadata.ontology_iri,
                    context.metadata.version_iri,
                ),
            )
        )
    active_counts = Counter(
        NAMESPACE_ASPECTS[term.namespace]
        for term in ontology.terms.values()
        if term.is_active and term.namespace in NAMESPACE_ASPECTS
    )
    active_terms_by_aspect = tuple(
        (aspect, active_counts.get(aspect, 0)) for aspect in GOAspect
    )

    roots_by_aspect = {
        NAMESPACE_ASPECTS[namespace]: root
        for namespace, root in GO_ROOT_BY_NAMESPACE.items()
    }
    for aspect, root in roots_by_aspect.items():
        term = ontology.terms.get(root)
        if term is None or not term.is_active:
            issues.append(
                OntologyValidationIssue(
                    Severity.ERROR,
                    "MISSING_CANONICAL_ROOT",
                    f"missing active {aspect.value} root {root}",
                    subject_iri=root,
                )
            )

    is_a_parents: dict[str, set[str]] = defaultdict(set)
    safe_parents: dict[str, set[str]] = defaultdict(set)
    navigation_parents: dict[str, set[str]] = defaultdict(set)
    by_relation: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    cross_aspect_children: set[str] = set()
    for edge in context.edges:
        child_id = _go_id(edge.child_iri)
        parent_id = _go_id(edge.parent_iri)
        if edge.disposition is EdgeDispositionKind.CROSS_ASPECT and child_id:
            cross_aspect_children.add(child_id)
        if child_id is None or parent_id is None:
            continue
        by_relation[edge.relation_iri][child_id].add(parent_id)
        if edge.disposition in {
            EdgeDispositionKind.PROPAGATING,
            EdgeDispositionKind.NAVIGABLE_ONLY,
        }:
            navigation_parents[child_id].add(parent_id)
        if edge.disposition is EdgeDispositionKind.PROPAGATING:
            safe_parents[child_id].add(parent_id)
            if edge.relation_iri == IS_A_IRI:
                is_a_parents[child_id].add(parent_id)

    reachability_items: list[TermReachability] = []
    for term_id, term in sorted(ontology.terms.items()):
        if not term.is_active or term.namespace not in NAMESPACE_ASPECTS:
            continue
        aspect = NAMESPACE_ASPECTS[term.namespace]
        root = roots_by_aspect[aspect]
        findings: set[ReachabilityFinding] = set()
        if term_id == root:
            findings.add(ReachabilityFinding.ROOT_TERM)
        else:
            has_is_a_path = _reachable(term_id, root, is_a_parents)
            has_safe_path = _reachable(term_id, root, safe_parents)
            if has_is_a_path:
                findings.add(ReachabilityFinding.VALID_IS_A_ROOT_PATH)
            elif has_safe_path:
                findings.add(
                    ReachabilityFinding.NO_IS_A_ROOT_PATH_BUT_SAFE_PART_OF_PATH
                )
            else:
                findings.add(ReachabilityFinding.NO_SAFE_PATH_TO_ASPECT_ROOT)
        if term_id in cross_aspect_children:
            findings.add(ReachabilityFinding.CROSS_ASPECT_EDGE_IGNORED)
            if ReachabilityFinding.NO_SAFE_PATH_TO_ASPECT_ROOT in findings:
                findings.add(
                    ReachabilityFinding.NO_OWN_ASPECT_ROOT_AFTER_EDGE_FILTERING
                )
        reachability_items.append(
            TermReachability(
                term_id,
                aspect,
                frozenset(findings),
                bool(is_a_parents.get(term_id)),
            )
        )
    reachability = tuple(reachability_items)

    without_direct_is_a = tuple(
        item.term_id
        for item in reachability
        if ReachabilityFinding.ROOT_TERM not in item.findings
        and not item.has_direct_is_a_parent
    )
    without_is_a_path = tuple(
        item.term_id
        for item in reachability
        if ReachabilityFinding.ROOT_TERM not in item.findings
        and ReachabilityFinding.VALID_IS_A_ROOT_PATH not in item.findings
    )
    without_safe_path = tuple(
        item.term_id
        for item in reachability
        if ReachabilityFinding.NO_SAFE_PATH_TO_ASPECT_ROOT in item.findings
    )

    outgoing_edges: dict[str, list[EdgeDispositionKind]] = defaultdict(list)
    external_outgoing: dict[str, list[bool]] = defaultdict(list)
    for edge in context.edges:
        child_id = _go_id(edge.child_iri)
        if child_id is None:
            continue
        outgoing_edges[child_id].append(edge.disposition)
        external_outgoing[child_id].append(
            edge.disposition is EdgeDispositionKind.EXTERNAL_CONTEXT
        )
    only_non_propagating = tuple(
        term_id
        for term_id, dispositions in sorted(outgoing_edges.items())
        if dispositions
        and all(
            disposition is not EdgeDispositionKind.PROPAGATING
            for disposition in dispositions
        )
    )
    only_external = tuple(
        term_id
        for term_id, external_flags in sorted(external_outgoing.items())
        if external_flags and all(external_flags)
    )

    part_of_only = tuple(
        item.term_id
        for item in reachability
        if ReachabilityFinding.NO_IS_A_ROOT_PATH_BUT_SAFE_PART_OF_PATH in item.findings
    )
    cross_aspect_terms = tuple(
        item.term_id
        for item in reachability
        if ReachabilityFinding.CROSS_ASPECT_EDGE_IGNORED in item.findings
    )
    if without_safe_path:
        issues.append(
            OntologyValidationIssue(
                Severity.WARNING,
                "NO_SAFE_PATH_TO_ASPECT_ROOT",
                f"{len(without_safe_path)} active GO terms have no safe root path",
                examples=without_safe_path[:maximum_examples],
            )
        )
    if part_of_only:
        issues.append(
            OntologyValidationIssue(
                Severity.WARNING,
                "PART_OF_ONLY_ROOT_PATH_QUARANTINED",
                f"{len(part_of_only)} terms reach their root only through part_of",
                examples=part_of_only[:maximum_examples],
            )
        )
    if cross_aspect_terms:
        issues.append(
            OntologyValidationIssue(
                Severity.INFO,
                "CROSS_ASPECT_EDGES_IGNORED",
                f"{len(cross_aspect_terms)} terms have ignored cross-aspect edges",
                examples=cross_aspect_terms[:maximum_examples],
            )
        )

    property_adjacency = {
        descriptor.iri: set(descriptor.direct_superproperties)
        for descriptor in context.relations
    }
    subproperty_cycles = _strongly_connected_cycles(
        set(property_adjacency), property_adjacency
    )
    extracted_relation_cycles = tuple(
        RelationCycleRecord(
            relation_iri,
            cycle,
        )
        for relation_iri, adjacency in sorted(by_relation.items())
        for cycle in _strongly_connected_cycles(
            set(adjacency) | {node for values in adjacency.values() for node in values},
            adjacency,
        )
    )
    go_nodes = set(ontology.terms)
    semantic_navigation_cycles = _strongly_connected_cycles(
        go_nodes, navigation_parents
    )
    scoring_hierarchy_cycles = _strongly_connected_cycles(go_nodes, safe_parents)
    if scoring_hierarchy_cycles:
        issues.append(
            OntologyValidationIssue(
                Severity.ERROR,
                "SCORING_HIERARCHY_CYCLE",
                "is_a/part_of scoring hierarchy contains a cycle",
                examples=tuple(
                    ",".join(cycle.nodes)
                    for cycle in scoring_hierarchy_cycles[:maximum_examples]
                ),
            )
        )
    if semantic_navigation_cycles:
        issues.append(
            OntologyValidationIssue(
                Severity.INFO,
                "SEMANTIC_NAVIGATION_CYCLE",
                "semantic navigation projection contains non-scoring cycles",
                examples=tuple(
                    ",".join(cycle.nodes)
                    for cycle in semantic_navigation_cycles[:maximum_examples]
                ),
            )
        )
    if subproperty_cycles:
        issues.append(
            OntologyValidationIssue(
                Severity.WARNING,
                "SUBPROPERTY_CYCLE",
                "object-property hierarchy contains a cycle",
                examples=tuple(
                    ",".join(cycle.nodes)
                    for cycle in subproperty_cycles[:maximum_examples]
                ),
            )
        )
    for shape, count in context.unsupported_axiom_counts:
        issues.append(
            OntologyValidationIssue(
                Severity.WARNING,
                "UNSUPPORTED_AXIOM_SHAPE",
                f"{count} expressions have shape {shape}",
                examples=(shape,),
            )
        )

    unknown_relations = tuple(
        descriptor.iri
        for descriptor in context.relations
        if descriptor.iri not in context.relation_policy.navigable_iris
    )
    if unknown_relations:
        issues.append(
            OntologyValidationIssue(
                Severity.WARNING,
                "UNKNOWN_RELATIONS_EXCLUDED",
                f"{len(unknown_relations)} relation IRIs have no approved use",
                examples=unknown_relations[:maximum_examples],
            )
        )
    disposition_counter = Counter(edge.disposition for edge in context.edges)
    disposition_counts = tuple(
        (kind, disposition_counter.get(kind, 0)) for kind in EdgeDispositionKind
    )
    issues_tuple = tuple(
        sorted(
            issues,
            key=lambda issue: (
                issue.severity.value,
                issue.code,
                issue.subject_iri or "",
                issue.relation_iri or "",
                issue.message,
            ),
        )
    )
    payload = _canonical_report_payload(
        ontology_fingerprint=context.fingerprint.value,
        preflight_policy_fingerprint=active_policy.identity.fingerprint,
        active_terms_by_aspect=active_terms_by_aspect,
        disposition_counts=disposition_counts,
        reachability=reachability,
        cycles=scoring_hierarchy_cycles,
        issues=issues_tuple,
    )
    report = OntologyPreflightReport(
        report_id=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        ontology_fingerprint=context.fingerprint.value,
        import_policy_fingerprint=context.metadata.import_policy.fingerprint,
        relation_policy_fingerprint=context.relation_policy.identity.fingerprint,
        preflight_policy_fingerprint=active_policy.identity.fingerprint,
        active_terms_by_aspect=active_terms_by_aspect,
        obsolete_count=sum(term.obsolete for term in ontology.terms.values()),
        deprecated_count=sum(term.deprecated for term in ontology.terms.values()),
        external_class_count=sum(
            not iri.startswith("http://purl.obolibrary.org/obo/GO_")
            for iri in context.complete_graph.class_iris
        ),
        relations=context.relations,
        edge_disposition_counts=disposition_counts,
        reachability=reachability,
        active_terms_without_direct_is_a_parent=without_direct_is_a,
        active_terms_without_is_a_root_path=without_is_a_path,
        active_terms_without_safe_root_path=without_safe_path,
        terms_with_only_non_propagating_edges=only_non_propagating,
        terms_linked_only_to_external_classes=only_external,
        subproperty_cycles=subproperty_cycles,
        extracted_relation_cycles=extracted_relation_cycles,
        semantic_navigation_cycles=semantic_navigation_cycles,
        scoring_hierarchy_cycles=scoring_hierarchy_cycles,
        unknown_relations=unknown_relations,
        unsupported_axiom_counts=context.unsupported_axiom_counts,
        issues=issues_tuple,
    )
    if strict and not report.is_valid:
        raise OntologyPreflightError(report)
    return report
