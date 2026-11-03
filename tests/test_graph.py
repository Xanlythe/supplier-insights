import json
import sqlite3

import pytest

from supplier_insights.graph import AmbiguousMatch, build_graph, select_candidate
from supplier_insights.http import SourceError
from supplier_insights.models import Neighbor, Node


class FakeSource:
    name = "wikidata"

    def __init__(self, nodes, relations=None):
        self.nodes = nodes
        self.relations = relations or {}
        self.warnings = []
        self.calls = []

    def get(self, identifier):
        self.calls.append(("get", identifier))
        value = self.nodes[identifier]
        if isinstance(value, Exception):
            raise value
        return value

    def neighbors(self, identifier, direction):
        self.calls.append((direction, identifier))
        return self.relations.get((identifier, direction), [])


def node(qid, name="Example", lei=None):
    ids = {"qid": [qid]}
    if lei:
        ids["lei"] = [lei]
    return Node(f"wikidata:{qid}", name, "Organization", ids, sources=["https://example.test"])


def test_names_never_merge_and_lei_does():
    a = node("Q1", lei="A" * 20)
    b = Node("gleif:a", "Different label", "Company", {"lei": ["A" * 20]})
    c = node("Q2")
    graph = build_graph(
        [(FakeSource({"Q1": a, "Q2": c}), "Q1"), (FakeSource({"a": b}), "a")], depth=0
    )
    assert len(graph.nodes) == 1
    separate = build_graph([(FakeSource({"Q1": a}), "Q1"), (FakeSource({"Q2": c}), "Q2")], depth=0)
    assert len(separate.nodes) == 2
    assert separate.warnings


def test_graph_walk_is_directional_bounded_and_cycle_safe():
    source = FakeSource(
        {f"Q{i}": node(f"Q{i}") for i in range(1, 5)},
        {
            ("Q1", "parents"): [Neighbor("Q2", "parent_organization", "source")],
            ("Q2", "parents"): [Neighbor("Q1", "parent_organization", "source")],
            ("Q1", "children"): [Neighbor("Q3", "ownership", "source")],
            ("Q2", "children"): [Neighbor("Q4", "ownership", "source")],
        },
    )
    graph = build_graph([(source, "Q1")], depth=4)
    assert len(graph.nodes) == 3
    assert all(n.identifiers["qid"] != ["Q4"] for n in graph.nodes.values())
    assert len(source.calls) < 20
    bounded = build_graph([(source, "Q1")], depth=2, max_nodes=2)
    assert len(bounded.nodes) == 2
    assert any("max-nodes" in w for w in bounded.warnings)


def test_partial_failures_are_reported_but_total_failure_raises():
    source = FakeSource({"Q1": node("Q1"), "Q2": SourceError("offline")})
    partial = build_graph([(source, "Q1"), (source, "Q2")], depth=0)
    assert "offline" in " ".join(partial.warnings)
    with pytest.raises(SourceError):
        build_graph([(source, "Q2")], depth=0)


def test_ambiguous_search_never_chooses_first():
    candidates = [{"id": "Q1", "name": "Example"}, {"id": "Q2", "name": "Example"}]
    with pytest.raises(AmbiguousMatch, match="Q2"):
        select_candidate(candidates, "wikidata")
    assert select_candidate(candidates[:1], "wikidata") == "Q1"
    assert select_candidate([], "wikidata") is None


def test_unique_exact_organization_name_is_preferred_over_broader_matches():
    candidates = [
        {"id": "Q1406", "name": "Microsoft Windows"},
        {"id": "Q2283", "name": "Microsoft"},
        {"id": "Q135288", "name": "Microsoft Store"},
    ]
    assert select_candidate(candidates, "wikidata", "  MICROSOFT  ") == "Q2283"


def test_multiple_exact_name_organizations_still_require_selection():
    candidates = [{"id": "Q1", "name": "Example"}, {"id": "Q2", "name": "EXAMPLE"}]
    with pytest.raises(AmbiguousMatch, match="select --qid"):
        select_candidate(candidates, "wikidata", "Example")


def test_conflicting_known_identifiers_do_not_merge():
    a = node("Q1", lei="A" * 20)
    b = node("Q1", lei="B" * 20)
    b.id = "other:Q1"
    graph = build_graph([(FakeSource({"a": a}), "a"), (FakeSource({"b": b}), "b")], depth=0)
    assert len(graph.nodes) == 2
    assert "conflict" in " ".join(graph.warnings).lower()


def test_identifier_enrichment_still_merges_at_node_limit():
    a = node("Q1", lei="A" * 20)
    b = Node("gleif:a", "Example Legal Name", "Company", {"lei": ["A" * 20]})
    first, second = FakeSource({"Q1": a}), FakeSource({"A" * 20: b})
    second.name = "gleif"
    graph = build_graph([(first, "Q1"), (second, "A" * 20)], depth=0, max_nodes=1)
    assert len(graph.nodes) == 1
    assert graph.nodes[graph.roots[0]].aliases == ["Example Legal Name"]
    assert graph.nodes[graph.roots[0]].schema == "Company"


def test_gleif_descendants_enrich_from_wikidata_without_expanding_siblings():
    lei = "A" * 20
    wd = FakeSource({"Q1": node("Q1", lei=lei), "Q2": node("Q2", lei="B" * 20)})
    gleif = FakeSource(
        {
            lei: Node("gleif:a", "Example", "Company", {"lei": [lei]}),
            "B" * 20: Node("gleif:b", "Child", "Company", {"lei": ["B" * 20]}),
        },
        {(lei, "children"): [Neighbor("B" * 20, "accounting_direct_parent", "source")]},
    )
    gleif.name = "gleif"
    graph = build_graph(
        [(gleif, lei)],
        depth=1,
        additional_sources=[wd, gleif],
        resolve_lei=lambda value: ["Q1"] if value == lei else ["Q2"],
    )
    assert len(graph.nodes) == 2
    assert all(n.identifiers.get("qid") for n in graph.nodes.values())


def test_tree_deduplicates_shared_descendants():
    source = FakeSource(
        {f"Q{i}": node(f"Q{i}") for i in range(1, 5)},
        {
            ("Q1", "children"): [
                Neighbor("Q2", "ownership", "source"),
                Neighbor("Q3", "ownership", "source"),
            ],
            ("Q2", "children"): [Neighbor("Q4", "ownership", "source")],
            ("Q3", "children"): [Neighbor("Q4", "ownership", "source")],
        },
    )
    assert "[cycle/reference]" in build_graph([(source, "Q1")], depth=3).tree()


def test_shorter_cross_source_path_revisits_depth_limited_node():
    root_lei, child_lei = "A" * 20, "C" * 20
    wd = FakeSource(
        {
            "Q1": node("Q1", lei=root_lei),
            "Q2": node("Q2"),
            "Q3": node("Q3", lei=child_lei),
            "Q4": node("Q4"),
        },
        {
            ("Q1", "children"): [Neighbor("Q2", "ownership", "source")],
            ("Q2", "children"): [Neighbor("Q3", "ownership", "source")],
            ("Q3", "children"): [Neighbor("Q4", "ownership", "source")],
        },
    )
    gleif = FakeSource(
        {
            root_lei: Node("gleif:root", "Root", "Company", {"lei": [root_lei]}),
            child_lei: Node("gleif:child", "Child", "Company", {"lei": [child_lei]}),
        },
        {(root_lei, "children"): [Neighbor(child_lei, "accounting_direct_parent", "source")]},
    )
    gleif.name = "gleif"
    graph = build_graph(
        [(wd, "Q1"), (gleif, root_lei)],
        depth=2,
        resolve_lei=lambda value: ["Q3"] if value == child_lei else ["Q1"],
    )
    assert any("Q4" in n.identifiers.get("qid", []) for n in graph.nodes.values())


def test_graph_is_persisted_with_provenance_and_replaces_last_lookup(tmp_path):
    source = FakeSource(
        {"Q1": node("Q1"), "Q2": node("Q2")},
        {("Q1", "parents"): [Neighbor("Q2", "ownership", "source")]},
    )
    path = tmp_path / "graph.sqlite"
    graph = build_graph([(source, "Q1")], depth=1)
    graph.save(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT kind, source FROM edges").fetchall() == [("ownership", "source")]
        assert db.execute("SELECT id FROM roots").fetchone()[0] == "wikidata:Q1"
        body = json.loads(db.execute("SELECT body FROM nodes WHERE id='wikidata:Q1'").fetchone()[0])
        assert body["identifiers"] == {"qid": ["Q1"]}
    build_graph([(source, "Q2")], depth=0).save(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM edges").fetchone()[0] == 0
