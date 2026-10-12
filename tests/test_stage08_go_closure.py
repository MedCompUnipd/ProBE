"""Scientific contracts for the Stage 08 strict GO closure kernel."""

import pytest

from probe.stage08_go_closure import GoClosureIndex


@pytest.fixture
def index():
    return GoClosureIndex(
        {
            "F1": "F",
            "F2": "F",
            "F3": "F",
            "F4": "F",
            "P1": "P",
            "P2": "P",
            "C1": "C",
        },
        [
            ("F2", "is_a", "F1"),
            ("F3", "part_of", "F2"),
            ("F4", "is_a", "F1"),
            ("P2", "part_of", "P1"),
        ],
    )


def test_graph_counts(index):
    assert index.node_count == 7
    assert index.edge_count == 4


def test_positive_to_ancestors_includes_self(index):
    assert index.ancestors("F3") == ("F1", "F2", "F3")


def test_negative_to_descendants_includes_self(index):
    assert index.descendants("F1") == ("F1", "F2", "F3", "F4")


def test_negative_propagates_through_part_of(index):
    assert index.descendants("F2") == ("F2", "F3")


def test_positive_does_not_propagate_to_children(index):
    assert index.ancestors("F1") == ("F1",)


def test_negative_does_not_propagate_to_parents(index):
    assert index.descendants("F3") == ("F3",)


def test_unconnected_root(index):
    assert index.ancestors("C1") == ("C1",)
    assert index.descendants("C1") == ("C1",)


def test_aspects_remain_disjoint(index):
    assert all(index.aspect(t) == "F" for t in index.ancestors("F3"))
    assert all(index.aspect(t) == "F" for t in index.descendants("F1"))


def test_part_of_is_not_reversed(index):
    assert "F3" not in index.ancestors("F2")


def test_duplicate_edges_deduplicated():
    graph = GoClosureIndex(
        {"A": "F", "B": "F"},
        [("B", "is_a", "A"), ("B", "is_a", "A")],
    )
    assert graph.edge_count == 1


def test_multiple_evidence_edge_types_are_preserved():
    graph = GoClosureIndex(
        {"A": "F", "B": "F"},
        [("B", "is_a", "A"), ("B", "part_of", "A")],
    )
    assert graph.edge_count == 2
    assert graph.direct_parent_edges("B") == (
        ("is_a", "A"),
        ("part_of", "A"),
    )
    assert graph.ancestors("B") == ("A", "B")


@pytest.mark.parametrize(
    "relation",
    ["regulates", "negatively_regulates", "has_part", "occurs_in"],
)
def test_non_inheritable_edges_are_rejected(relation):
    with pytest.raises(ValueError, match="Non-inheritable"):
        GoClosureIndex({"A": "F", "B": "F"}, [("B", relation, "A")])


def test_cross_aspect_rejected_by_default():
    with pytest.raises(ValueError, match="Cross-aspect"):
        GoClosureIndex({"A": "F", "B": "P"}, [("B", "part_of", "A")])


def test_cross_aspect_can_be_reported_without_inheritance():
    graph = GoClosureIndex(
        {"A": "F", "B": "P"},
        [("B", "part_of", "A")],
        cross_aspect="ignore",
    )
    assert graph.edge_count == 0
    assert graph.ignored_cross_aspect_edges == 1
    assert graph.ancestors("B") == ("B",)


def test_cycles_rejected():
    with pytest.raises(ValueError, match="cycle"):
        GoClosureIndex(
            {"A": "F", "B": "F"},
            [("A", "is_a", "B"), ("B", "part_of", "A")],
        )


def test_self_loop_rejected():
    with pytest.raises(ValueError, match="self-loop"):
        GoClosureIndex({"A": "F"}, [("A", "is_a", "A")])


def test_unknown_go_fails_closed(index):
    with pytest.raises(KeyError, match="pinned GO graph"):
        index.ancestors("GO:9999999")


def test_missing_endpoint_fails_closed():
    with pytest.raises(ValueError, match="unknown term"):
        GoClosureIndex({"A": "F"}, [("A", "is_a", "B")])


def test_direct_relations_returned_without_inference(index):
    assert index.direct_parents("F3") == ("F2",)
    assert index.direct_children("F2") == ("F3",)
    assert index.direct_parent_edges("F3") == (("part_of", "F2"),)


def test_witness_positive_path(index):
    assert index.witness_path("F3", "F1", direction="positive") == (
        "F3",
        "F2",
        "F1",
    )


def test_witness_not_path(index):
    assert index.witness_path("F1", "F3", direction="not") == (
        "F1",
        "F2",
        "F3",
    )


def test_witness_non_reachable(index):
    assert index.witness_path("F1", "F3", direction="positive") is None


def test_witness_requires_known_direction(index):
    with pytest.raises(ValueError, match="direction"):
        index.witness_path("F2", "F1", direction="left")


def test_independent_graphs_have_no_shared_cache(index):
    first = GoClosureIndex({"A": "F", "B": "F"}, [("B", "is_a", "A")])
    second = GoClosureIndex({"A": "F", "B": "F"}, [])
    assert first.ancestors("B") == ("A", "B")
    assert second.ancestors("B") == ("B",)


def test_invalid_cross_aspect_option():
    with pytest.raises(ValueError, match="cross_aspect"):
        GoClosureIndex({"A": "F"}, [], cross_aspect="silent")


def test_empty_graph_rejected():
    with pytest.raises(ValueError, match="at least one"):
        GoClosureIndex({}, [])


def test_adapter_uses_direct_edges_and_ignores_cross_aspect():
    class Term:
        def __init__(self, namespace, active=True):
            self.namespace = namespace
            self.is_active = active

    class Ontology:
        terms = {
            "A": Term("F"),
            "B": Term("F"),
            "C": Term("P"),
            "D": Term("F", active=False),
        }

        def parents(self, term, relations):
            if relations == ("is_a",):
                return {"B": ["A"], "D": ["A"]}.get(term, [])
            return {"B": ["C"]}.get(term, [])

    graph = GoClosureIndex.from_gene_ontology(Ontology())
    assert graph.node_count == 3
    assert graph.edge_count == 1
    assert graph.ignored_cross_aspect_edges == 1
    assert graph.ancestors("B") == ("A", "B")


def test_immutability_of_result(index):
    with pytest.raises(TypeError):
        index.ancestors("F3")[0] = "MODIFIED"


def test_cached_closures_do_not_pin_discarded_graphs():
    import gc
    import weakref

    graph = GoClosureIndex(
        {"A": "F", "B": "F"},
        [("B", "is_a", "A")],
    )
    assert graph.ancestors("B") == ("A", "B")
    assert graph.descendants("A") == ("A", "B")
    reference = weakref.ref(graph)
    del graph
    gc.collect()
    assert reference() is None


def test_closure_caches_are_bounded_per_instance():
    names = [f"GO:{number:07d}" for number in range(1030)]
    graph = GoClosureIndex({name: "F" for name in names}, [])
    for name in names:
        assert graph.ancestors(name) == (name,)
        assert graph.descendants(name) == (name,)
    assert len(graph._ancestor_cache) == 1024
    assert len(graph._descendant_cache) == 1024
    assert names[0] not in graph._ancestor_cache
    assert names[0] not in graph._descendant_cache
