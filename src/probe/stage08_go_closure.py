"""Read-only GO graph closure for Stage 08 downstream interpretation.

Only same-aspect is_a and existential part_of GO edges are inherited.
Graph traversal alone never turns a NOT assertion into a scoring decision.
"""

from __future__ import annotations

from collections import OrderedDict, deque
from collections.abc import Iterable, Mapping
from types import MappingProxyType

ALLOWED_RELATIONS = frozenset({"is_a", "part_of"})


class GoClosureIndex:
    """Immutable DAG with direct-edge and transitive-closure interfaces.

    Edges point from a more specific GO term to a more general term.
    The index includes the source term in both closure directions.
    """

    def __init__(
        self,
        aspects: Mapping[str, str],
        edges: Iterable[tuple[str, str, str]],
        *,
        cross_aspect: str = "error",
    ) -> None:
        if cross_aspect not in {"error", "ignore"}:
            raise ValueError("cross_aspect must be 'error' or 'ignore'")
        if not aspects:
            raise ValueError("GO graph must contain at least one active term")
        labels = dict(aspects)
        if any(
            not isinstance(term, str)
            or not term
            or not isinstance(aspect, str)
            or not aspect
            for term, aspect in labels.items()
        ):
            raise ValueError("GO term IDs and aspects must be nonempty strings")
        normalized: set[tuple[str, str, str]] = set()
        rejected: set[tuple[str, str, str]] = set()
        for child, relation, parent in edges:
            if relation not in ALLOWED_RELATIONS:
                raise ValueError(f"Non-inheritable GO relation: {relation}")
            if child not in labels or parent not in labels:
                raise ValueError(f"GO edge refers to inactive/unknown term: {child}")
            item = (child, relation, parent)
            if labels[child] != labels[parent]:
                if cross_aspect == "error":
                    raise ValueError(f"Cross-aspect GO inheritance edge: {item}")
                rejected.add(item)
                continue
            if child == parent:
                raise ValueError(f"GO self-loop: {child}")
            normalized.add(item)
        parents: dict[str, set[str]] = {node: set() for node in labels}
        children: dict[str, set[str]] = {node: set() for node in labels}
        parent_edges: dict[str, set[tuple[str, str]]] = {node: set() for node in labels}
        for child, relation, parent in normalized:
            parents[child].add(parent)
            children[parent].add(child)
            parent_edges[child].add((relation, parent))
        self._require_acyclic(parents, children)
        self._aspects = MappingProxyType(labels)
        self._parents = MappingProxyType(
            {node: tuple(sorted(values)) for node, values in parents.items()}
        )
        self._children = MappingProxyType(
            {node: tuple(sorted(values)) for node, values in children.items()}
        )
        self._parent_edges = MappingProxyType(
            {node: tuple(sorted(values)) for node, values in parent_edges.items()}
        )
        self._edge_count = len(normalized)
        self._ignored_cross_aspect = len(rejected)
        self._ancestor_cache: OrderedDict[str, tuple[str, ...]] = OrderedDict()
        self._descendant_cache: OrderedDict[str, tuple[str, ...]] = OrderedDict()

    @staticmethod
    def _require_acyclic(
        parents: Mapping[str, set[str]],
        children: Mapping[str, set[str]],
    ) -> None:
        pending = {term: len(children[term]) for term in parents}
        queue = deque(term for term in sorted(pending) if pending[term] == 0)
        visited = 0
        while queue:
            term = queue.popleft()
            visited += 1
            for parent in parents[term]:
                pending[parent] -= 1
                if pending[parent] == 0:
                    queue.append(parent)
        if visited != len(parents):
            raise ValueError("GO inheritance graph contains a cycle")

    @classmethod
    def from_gene_ontology(cls, ontology: object) -> GoClosureIndex:
        """Project the already-verified ProBE GeneOntology loader.

        This deliberately skips cross-aspect links from the pinned OWL.
        It does not read ontology files or perform independent OWL parsing.
        """
        terms = ontology.terms
        active = {
            term_id: term.namespace for term_id, term in terms.items() if term.is_active
        }
        edges = (
            (term_id, relation, parent)
            for term_id in sorted(active)
            for relation in sorted(ALLOWED_RELATIONS)
            for parent in ontology.parents(term_id, relations=(relation,))
            if parent in active
        )
        return cls(active, edges, cross_aspect="ignore")

    @property
    def node_count(self) -> int:
        return len(self._aspects)

    @property
    def edge_count(self) -> int:
        return self._edge_count

    @property
    def ignored_cross_aspect_edges(self) -> int:
        return self._ignored_cross_aspect

    def aspect(self, term: str) -> str:
        return self._aspects[term]

    def direct_parent_edges(self, term: str) -> tuple[tuple[str, str], ...]:
        return self._parent_edges[term]

    def direct_parents(self, term: str) -> tuple[str, ...]:
        return self._parents[term]

    def direct_children(self, term: str) -> tuple[str, ...]:
        return self._children[term]

    def ancestors(self, term: str) -> tuple[str, ...]:
        """Positive GO closure, including the annotated term itself."""
        return self._memoized_closure(term, self._parents, self._ancestor_cache)

    def descendants(self, term: str) -> tuple[str, ...]:
        """NOT GO closure, including the negated term itself."""
        return self._memoized_closure(term, self._children, self._descendant_cache)

    def _memoized_closure(
        self,
        term: str,
        neighbors: Mapping[str, tuple[str, ...]],
        cache: OrderedDict[str, tuple[str, ...]],
    ) -> tuple[str, ...]:
        result = cache.get(term)
        if result is not None:
            cache.move_to_end(term)
            return result
        result = self._closure(term, neighbors)
        cache[term] = result
        if len(cache) > 1024:
            cache.popitem(last=False)
        return result

    def _closure(
        self,
        term: str,
        neighbors: Mapping[str, tuple[str, ...]],
    ) -> tuple[str, ...]:
        if term not in self._aspects:
            raise KeyError(f"Term not present in pinned GO graph: {term}")
        visited = {term}
        queue = deque([term])
        while queue:
            for related in neighbors[queue.popleft()]:
                if related not in visited:
                    visited.add(related)
                    queue.append(related)
        return tuple(sorted(visited))

    def witness_path(
        self,
        source: str,
        destination: str,
        *,
        direction: str,
    ) -> tuple[str, ...] | None:
        """Return one shortest deterministic propagation path if it exists."""
        if direction not in {"positive", "not"}:
            raise ValueError("direction must be 'positive' or 'not'")
        neighbors = self._parents if direction == "positive" else self._children
        if source not in self._aspects or destination not in self._aspects:
            raise KeyError("Witness path requires GO terms in the pinned graph")
        paths = {source: (source,)}
        queue = deque([source])
        while queue:
            current = queue.popleft()
            if current == destination:
                return paths[current]
            for nxt in neighbors[current]:
                if nxt not in paths:
                    paths[nxt] = (*paths[current], nxt)
                    queue.append(nxt)
        return None
