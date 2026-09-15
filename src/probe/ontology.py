"""Gene Ontology graph semantics independent from OWL serialization."""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from probe.source import Source
from probe.validation import ValidationReport

IS_A = "is_a"
PART_OF = "part_of"
HAS_PART = "has_part"
REGULATES = "regulates"
POSITIVELY_REGULATES = "positively_regulates"
NEGATIVELY_REGULATES = "negatively_regulates"

# These edges can be followed from a more specific GO term towards a broader or
# causally related term. This does not imply that an annotation can be inherited
# unchanged over every edge.
NAVIGABLE_RELATIONS = frozenset(
    {IS_A, PART_OF, REGULATES, POSITIVELY_REGULATES, NEGATIVELY_REGULATES}
)

# Positive annotations propagate upward, and NOT constraints propagate downward,
# only over relations that preserve the gene-product-to-term meaning.
ANNOTATION_PROPAGATION_RELATIONS = frozenset({IS_A, PART_OF})
DEFAULT_RELATIONS = ANNOTATION_PROPAGATION_RELATIONS

GO_ROOTS = frozenset({"GO:0003674", "GO:0008150", "GO:0005575"})
GO_ROOT_BY_NAMESPACE = {
    "molecular_function": "GO:0003674",
    "biological_process": "GO:0008150",
    "cellular_component": "GO:0005575",
}


class TermStatus(StrEnum):
    ACTIVE = "active"
    ALTERNATE_ID = "alternate_id"
    REPLACED = "replaced"
    OBSOLETE = "obsolete"
    DEPRECATED = "deprecated"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class OntologyTerm:
    identifier: str
    label: str = ""
    namespace: str = ""
    obsolete: bool = False
    deprecated: bool = False
    alternate_ids: tuple[str, ...] = ()
    replaced_by: tuple[str, ...] = ()
    consider: tuple[str, ...] = ()

    @property
    def is_active(self) -> bool:
        return not self.obsolete and not self.deprecated


@dataclass(frozen=True, slots=True)
class TermResolution:
    requested_id: str
    canonical_id: str | None
    status: TermStatus
    candidates: tuple[str, ...] = ()

    @property
    def is_usable(self) -> bool:
        return self.canonical_id is not None


class GeneOntology:
    """A GO snapshot with explicit relation and annotation semantics."""

    def __init__(
        self,
        terms: Iterable[OntologyTerm],
        edges: Iterable[tuple[str, str, str]] = (),
        *,
        release: str | None = None,
        source: Source | None = None,
    ) -> None:
        self.terms = {term.identifier: term for term in terms}
        self.release = release
        self.source = source
        self._alternate_ids: dict[str, str] = {}
        for term in self.terms.values():
            for alternate_id in term.alternate_ids:
                self._alternate_ids[alternate_id] = term.identifier

        parents: dict[str, dict[str, set[str]]] = {}
        children: dict[str, dict[str, set[str]]] = {}
        for child, relation, parent in edges:
            parents.setdefault(child, {}).setdefault(relation, set()).add(parent)
            children.setdefault(parent, {}).setdefault(relation, set()).add(child)
        self._parents = parents
        self._children = children

    @classmethod
    def from_owl(
        cls,
        source: Source | str | Path,
        *,
        strict: bool = True,
        report: ValidationReport | None = None,
    ) -> GeneOntology:
        from probe.parsing.owl import OwlLoader

        return OwlLoader().load(source, strict=strict, report=report)

    def __contains__(self, term_id: str) -> bool:
        return self.resolve(term_id).is_usable

    def __len__(self) -> int:
        return len(self.terms)

    def raw_term(self, term_id: str) -> OntologyTerm | None:
        canonical = (
            term_id if term_id in self.terms else self._alternate_ids.get(term_id)
        )
        return self.terms.get(canonical) if canonical else None

    def resolve(self, term_id: str) -> TermResolution:
        """Resolve an annotation ID without silently accepting invalid terms."""

        canonical = (
            term_id if term_id in self.terms else self._alternate_ids.get(term_id)
        )
        if canonical is None:
            return TermResolution(term_id, None, TermStatus.UNKNOWN)

        term = self.terms[canonical]
        if term.is_active:
            status = (
                TermStatus.ACTIVE
                if canonical == term_id
                else TermStatus.ALTERNATE_ID
            )
            return TermResolution(term_id, canonical, status)

        replacements = tuple(
            sorted(
                {
                    replacement
                    for candidate in term.replaced_by
                    if (replacement := self._resolve_active_id(candidate)) is not None
                }
            )
        )
        if len(replacements) == 1:
            return TermResolution(
                term_id,
                replacements[0],
                TermStatus.REPLACED,
                replacements,
            )

        status = TermStatus.OBSOLETE if term.obsolete else TermStatus.DEPRECATED
        candidates = replacements or tuple(sorted(term.consider))
        return TermResolution(term_id, None, status, candidates)

    def _resolve_active_id(self, term_id: str) -> str | None:
        canonical = (
            term_id if term_id in self.terms else self._alternate_ids.get(term_id)
        )
        if canonical is None or not self.terms[canonical].is_active:
            return None
        return canonical

    def resolve_id(self, term_id: str) -> str | None:
        """Return an active canonical ID, including an unambiguous replacement."""

        return self.resolve(term_id).canonical_id

    def term(self, term_id: str) -> OntologyTerm | None:
        resolved = self.resolve_id(term_id)
        return self.terms.get(resolved) if resolved else None

    @staticmethod
    def _related(
        graph: Mapping[str, Mapping[str, set[str]]],
        term_id: str,
        relations: Iterable[str],
    ) -> frozenset[str]:
        by_relation = graph.get(term_id, {})
        return frozenset(
            related
            for relation in relations
            for related in by_relation.get(relation, ())
        )

    def parents(
        self,
        term_id: str,
        *,
        relations: Iterable[str] = NAVIGABLE_RELATIONS,
    ) -> frozenset[str]:
        resolved = self.resolve_id(term_id)
        if resolved is None:
            return frozenset()
        return self._related(self._parents, resolved, relations)

    def children(
        self,
        term_id: str,
        *,
        relations: Iterable[str] = NAVIGABLE_RELATIONS,
    ) -> frozenset[str]:
        resolved = self.resolve_id(term_id)
        if resolved is None:
            return frozenset()
        return self._related(self._children, resolved, relations)

    def ancestors(
        self,
        term_id: str,
        *,
        relations: Iterable[str] = NAVIGABLE_RELATIONS,
        include_self: bool = False,
    ) -> frozenset[str]:
        return self._closure(
            term_id,
            graph=self._parents,
            relations=relations,
            include_self=include_self,
        )

    def descendants(
        self,
        term_id: str,
        *,
        relations: Iterable[str] = NAVIGABLE_RELATIONS,
        include_self: bool = False,
    ) -> frozenset[str]:
        return self._closure(
            term_id,
            graph=self._children,
            relations=relations,
            include_self=include_self,
        )

    def _closure(
        self,
        term_id: str,
        *,
        graph: Mapping[str, Mapping[str, set[str]]],
        relations: Iterable[str],
        include_self: bool,
    ) -> frozenset[str]:
        resolved = self.resolve_id(term_id)
        if resolved is None:
            return frozenset()
        found = {resolved} if include_self else set()
        queue = deque(self._related(graph, resolved, relations))
        while queue:
            related = queue.popleft()
            if related in found:
                continue
            found.add(related)
            queue.extend(self._related(graph, related, relations) - found)
        return frozenset(found)

    def propagate(
        self,
        term_ids: Iterable[str],
        *,
        relations: Iterable[str] = ANNOTATION_PROPAGATION_RELATIONS,
    ) -> frozenset[str]:
        """Propagate positive annotations without changing their meaning."""

        propagated: set[str] = set()
        for term_id in term_ids:
            resolved = self.resolve_id(term_id)
            if resolved is not None:
                propagated.add(resolved)
                propagated.update(self.ancestors(resolved, relations=relations))
        return frozenset(propagated)

    def excluded_by_not(self, negated_term_ids: Iterable[str]) -> frozenset[str]:
        """Return terms forbidden by NOT assertions under the true-path rule."""

        excluded: set[str] = set()
        for term_id in negated_term_ids:
            resolved = self.resolve_id(term_id)
            if resolved is not None:
                excluded.add(resolved)
                excluded.update(
                    self.descendants(
                        resolved,
                        relations=ANNOTATION_PROPAGATION_RELATIONS,
                    )
                )
        return frozenset(excluded)

    def roots(
        self,
        *,
        namespace: str | None = None,
        relations: Iterable[str] = NAVIGABLE_RELATIONS,
    ) -> frozenset[str]:
        return frozenset(
            term.identifier
            for term in self.terms.values()
            if term.is_active
            and (namespace is None or term.namespace == namespace)
            and not self.parents(term.identifier, relations=relations)
        )

    def leaves(
        self,
        *,
        namespace: str | None = None,
        relations: Iterable[str] = NAVIGABLE_RELATIONS,
    ) -> frozenset[str]:
        return frozenset(
            term.identifier
            for term in self.terms.values()
            if term.is_active
            and (namespace is None or term.namespace == namespace)
            and not self.children(term.identifier, relations=relations)
        )

    def cumulative_counts(
        self,
        direct_counts: Mapping[str, int | float],
        *,
        relations: Iterable[str] = ANNOTATION_PROPAGATION_RELATIONS,
    ) -> dict[str, float]:
        """Propagate direct annotation counts to every admissible ancestor."""

        cumulative = {
            term_id: 0.0 for term_id, term in self.terms.items() if term.is_active
        }
        for term_id, count in direct_counts.items():
            if count < 0:
                raise ValueError("annotation counts must be non-negative")
            resolved = self.resolve_id(term_id)
            if resolved is None:
                continue
            for propagated in self.propagate([resolved], relations=relations):
                cumulative[propagated] = cumulative.get(propagated, 0.0) + float(count)
        return cumulative

    def information_content(
        self,
        direct_counts: Mapping[str, int | float],
        *,
        relations: Iterable[str] = ANNOTATION_PROPAGATION_RELATIONS,
        smoothing: float = 1.0,
    ) -> dict[str, float]:
        """Compute namespace-relative information content from direct counts."""

        if smoothing <= 0:
            raise ValueError("smoothing must be greater than zero")
        cumulative = self.cumulative_counts(direct_counts, relations=relations)
        values: dict[str, float] = {}
        for term_id, frequency in cumulative.items():
            namespace = self.terms[term_id].namespace
            root_id = GO_ROOT_BY_NAMESPACE.get(namespace)
            if root_id is None or root_id not in cumulative:
                continue
            root_frequency = cumulative[root_id]
            probability = (frequency + smoothing) / (root_frequency + smoothing)
            values[term_id] = -math.log(min(probability, 1.0))
        return values

    def simgic(
        self,
        left: Iterable[str],
        right: Iterable[str],
        information_content: Mapping[str, float],
        *,
        relations: Iterable[str] = ANNOTATION_PROPAGATION_RELATIONS,
    ) -> float:
        """Return the IC-weighted Jaccard similarity of two GO term sets."""

        left_closure = self.propagate(left, relations=relations)
        right_closure = self.propagate(right, relations=relations)
        union = left_closure | right_closure
        denominator = sum(information_content.get(term, 0.0) for term in union)
        if denominator == 0:
            return 0.0
        intersection = left_closure & right_closure
        numerator = sum(information_content.get(term, 0.0) for term in intersection)
        return numerator / denominator

    def validate(self) -> ValidationReport:
        report = ValidationReport()
        source = self.source.name if self.source else None
        for alternate_id, canonical_id in self._alternate_ids.items():
            if alternate_id in self.terms and alternate_id != canonical_id:
                report.error(
                    "AMBIGUOUS_ALTERNATE_ID",
                    f"{alternate_id} is both a canonical and alternate identifier",
                    source=source,
                )
        for child, by_relation in self._parents.items():
            if child not in self.terms:
                report.error(
                    "UNKNOWN_ONTOLOGY_CHILD",
                    f"edge references unknown child {child}",
                    source=source,
                )
            for parents in by_relation.values():
                for parent in parents:
                    if parent not in self.terms:
                        report.error(
                            "UNKNOWN_ONTOLOGY_PARENT",
                            f"edge references unknown parent {parent}",
                            source=source,
                        )
        if self.terms and not GO_ROOTS.intersection(self.terms):
            report.warning(
                "MISSING_GO_ROOTS",
                "ontology contains none of the three canonical GO roots",
                source=source,
            )
        if self._has_propagation_cycle():
            report.error(
                "ONTOLOGY_CYCLE",
                "is_a/part_of propagation graph contains a cycle",
                source=source,
            )
        return report

    def _has_propagation_cycle(self) -> bool:
        unvisited = set(self.terms)
        while unvisited:
            start = next(iter(unvisited))
            stack: list[tuple[str, bool]] = [(start, False)]
            active: set[str] = set()
            complete: set[str] = set()
            while stack:
                node, leaving = stack.pop()
                if leaving:
                    active.discard(node)
                    complete.add(node)
                    unvisited.discard(node)
                    continue
                if node in complete:
                    continue
                if node in active:
                    return True
                active.add(node)
                stack.append((node, True))
                for parent in self.parents(
                    node, relations=ANNOTATION_PROPAGATION_RELATIONS
                ):
                    if parent in active:
                        return True
                    if parent not in complete:
                        stack.append((parent, False))
        return False

    @property
    def edges(self) -> Mapping[str, Mapping[str, frozenset[str]]]:
        return {
            child: {
                relation: frozenset(parents)
                for relation, parents in by_relation.items()
            }
            for child, by_relation in self._parents.items()
        }
