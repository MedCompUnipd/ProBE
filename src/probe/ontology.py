"""Gene Ontology graph semantics independent from OWL serialization."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from probe.source import Source
from probe.validation import ValidationReport

DEFAULT_RELATIONS = frozenset({"is_a", "part_of"})
GO_ROOTS = frozenset({"GO:0003674", "GO:0008150", "GO:0005575"})


@dataclass(frozen=True, slots=True)
class OntologyTerm:
    identifier: str
    label: str = ""
    namespace: str = ""
    obsolete: bool = False
    alternate_ids: tuple[str, ...] = ()
    replaced_by: tuple[str, ...] = ()
    consider: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TermProjection:
    source_term: str
    target_terms: frozenset[str]
    distance: int | None
    kind: str

    @property
    def is_mappable(self) -> bool:
        return bool(self.target_terms)


class GeneOntology:
    """A GO release with explicit, relation-aware graph operations."""

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
        for child, relation, parent in edges:
            parents.setdefault(child, {}).setdefault(relation, set()).add(parent)
        self._parents = parents

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
        return self.resolve_id(term_id) is not None

    def __len__(self) -> int:
        return len(self.terms)

    def resolve_id(self, term_id: str) -> str | None:
        if term_id in self.terms:
            return term_id
        return self._alternate_ids.get(term_id)

    def term(self, term_id: str) -> OntologyTerm | None:
        resolved = self.resolve_id(term_id)
        return self.terms.get(resolved) if resolved else None

    def parents(
        self,
        term_id: str,
        *,
        relations: Iterable[str] = DEFAULT_RELATIONS,
    ) -> frozenset[str]:
        resolved = self.resolve_id(term_id)
        if resolved is None:
            return frozenset()
        by_relation = self._parents.get(resolved, {})
        return frozenset(
            parent for relation in relations for parent in by_relation.get(relation, ())
        )

    def ancestors(
        self,
        term_id: str,
        *,
        relations: Iterable[str] = DEFAULT_RELATIONS,
        include_self: bool = False,
    ) -> frozenset[str]:
        resolved = self.resolve_id(term_id)
        if resolved is None:
            return frozenset()
        found = {resolved} if include_self else set()
        queue = deque(self.parents(resolved, relations=relations))
        while queue:
            parent = queue.popleft()
            if parent in found:
                continue
            found.add(parent)
            queue.extend(self.parents(parent, relations=relations) - found)
        return frozenset(found)

    def propagate(
        self,
        term_ids: Iterable[str],
        *,
        relations: Iterable[str] = DEFAULT_RELATIONS,
    ) -> frozenset[str]:
        propagated: set[str] = set()
        for term_id in term_ids:
            resolved = self.resolve_id(term_id)
            if resolved is not None:
                propagated.add(resolved)
                propagated.update(self.ancestors(resolved, relations=relations))
        return frozenset(propagated)

    def project_from(
        self,
        source_ontology: GeneOntology,
        term_id: str,
        *,
        relations: Iterable[str] = DEFAULT_RELATIONS,
    ) -> TermProjection:
        """Project a source release term to nearest terms in this release."""

        source_id = source_ontology.resolve_id(term_id)
        if source_id is None:
            return TermProjection(term_id, frozenset(), None, "unknown_source_term")
        if target_id := self.resolve_id(source_id):
            kind = "same" if target_id == source_id else "alternate_id"
            return TermProjection(source_id, frozenset({target_id}), 0, kind)

        visited = {source_id}
        frontier = {source_id}
        distance = 0
        while frontier:
            distance += 1
            next_frontier: set[str] = set()
            targets: set[str] = set()
            for current in frontier:
                for parent in source_ontology.parents(current, relations=relations):
                    if parent in visited:
                        continue
                    visited.add(parent)
                    next_frontier.add(parent)
                    if target := self.resolve_id(parent):
                        targets.add(target)
            if targets:
                return TermProjection(
                    source_id, frozenset(targets), distance, "nearest_ancestor"
                )
            frontier = next_frontier
        return TermProjection(source_id, frozenset(), None, "unmappable")

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
        """Detect a cycle without depending on a general graph library."""

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
                for parent in self.parents(node):
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
