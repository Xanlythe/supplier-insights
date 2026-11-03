import httpx
import pytest
from conftest import lei_record, statement, wd_record

from supplier_insights.http import SourceError
from supplier_insights.sources.gleif import Gleif
from supplier_insights.sources.wikidata import Wikidata


def test_wikidata_search_lists_organizations_not_products(make_http):
    def handle(request):
        if "query" in request.url.params:
            query = request.url.params["query"]
            assert "VALUES ?item" in query
            assert "wd:Q43229" in query
            return httpx.Response(
                200,
                json={
                    "results": {
                        "bindings": [
                            {"item": {"value": "http://www.wikidata.org/entity/Q2283"}},
                            {"item": {"value": "http://www.wikidata.org/entity/Q135288"}},
                        ]
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "search": [
                    {"id": "Q2283", "label": "Microsoft", "description": "technology corporation"},
                    {
                        "id": "Q1406",
                        "label": "Microsoft Windows",
                        "description": "operating system",
                    },
                    {"id": "Q135288", "label": "Microsoft Store", "description": "store"},
                ]
            },
        )

    candidates = Wikidata(make_http(handle)).search("Microsoft")
    assert [candidate["id"] for candidate in candidates] == ["Q2283", "Q135288"]


def test_wikidata_search_classification_failure_is_not_an_empty_search(make_http):
    def handle(request):
        if "query" in request.url.params:
            return httpx.Response(403)
        return httpx.Response(200, json={"search": [{"id": "Q2283", "label": "Microsoft"}]})

    with pytest.raises(SourceError, match="403"):
        Wikidata(make_http(handle)).search("Microsoft")


def test_wikidata_locations_are_not_inferred_operations(make_http):
    records = {
        "Q1": wd_record(
            claims={
                "P159": [statement({"id": "Q2"})],
                "P17": [statement({"id": "Q3"})],
                "P2541": [statement({"id": "Q4"})],
                "P31": [statement({"id": "Q270791"})],
            }
        ),
        "Q2": wd_record("Q2", "Headquarters City"),
        "Q3": wd_record("Q3", "Registration Country", {"P297": [statement("DE")]}),
        "Q4": wd_record("Q4", "Operating Country", {"P297": [statement("FR")]}),
    }

    def handle(request):
        if "query" in request.url.params:
            return httpx.Response(200, json={"results": {"bindings": []}})
        ids = request.url.params["ids"].split("|")
        return httpx.Response(200, json={"entities": {qid: records[qid] for qid in ids}})

    node = Wikidata(make_http(handle)).get("Q1")
    assert [f["value"]["country"] for f in node.facts if f["kind"] == "operating_country"] == ["FR"]
    assert [f["value"]["country"] for f in node.facts if f["kind"] == "associated_country"] == [
        "DE"
    ]
    assert node.flags[0]["kind"] == "soe"


def test_wikidata_reverse_relations_are_one_hop_and_bounded(make_http):
    def handle(request):
        if "query" in request.url.params:
            query = request.url.params["query"]
            if "VALUES ?item" in query:
                return httpx.Response(
                    200,
                    json={
                        "results": {
                            "bindings": [
                                {"item": {"value": f"http://www.wikidata.org/entity/Q{i}"}}
                                for i in [2, 3, 4]
                            ]
                        }
                    },
                )
            assert "LIMIT 3" in query
            assert "P749*" not in query
            return httpx.Response(
                200,
                json={
                    "results": {
                        "bindings": [
                            {
                                "item": {"value": f"http://www.wikidata.org/entity/Q{i}"},
                                "property": {"value": "P749"},
                            }
                            for i in [2, 3, 4]
                        ]
                    }
                },
            )
        return httpx.Response(200, json={"entities": {"Q1": wd_record()}})

    source = Wikidata(make_http(handle), relation_limit=2)
    neighbors = source.neighbors("Q1", "children")
    assert len(neighbors) == 2
    assert all(n.kind == "parent_organization" for n in neighbors)
    assert source.warnings


def test_reverse_owned_products_and_patents_are_not_subsidiaries(make_http):
    def handle(request):
        if "query" in request.url.params:
            query = request.url.params["query"]
            if "VALUES ?item" in query:
                return httpx.Response(
                    200,
                    json={
                        "results": {
                            "bindings": [
                                {"item": {"value": "http://www.wikidata.org/entity/Q364"}},
                            ]
                        }
                    },
                )
            return httpx.Response(
                200,
                json={
                    "results": {
                        "bindings": [
                            {
                                "item": {"value": f"http://www.wikidata.org/entity/{qid}"},
                                "property": {"value": "P127"},
                            }
                            for qid in ["Q364", "Q1406", "Q2"]
                        ]
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "entities": {
                    "Q2283": wd_record(
                        "Q2283", "Microsoft", {"P355": [statement({"id": "Q127278673"})]}
                    )
                }
            },
        )

    children = Wikidata(make_http(handle)).neighbors("Q2283", "children")
    assert {child.identifier for child in children} == {"Q364", "Q127278673"}


def test_explicit_subsidiaries_survive_reverse_query_failure(make_http):
    def handle(request):
        if "query" in request.url.params:
            return httpx.Response(403)
        return httpx.Response(
            200,
            json={
                "entities": {
                    "Q2283": wd_record("Q2283", "Microsoft", {"P355": [statement({"id": "Q364"})]})
                }
            },
        )

    source = Wikidata(make_http(handle))
    children = source.neighbors("Q2283", "children")
    assert [child.identifier for child in children] == ["Q364"]
    assert any("403" in warning for warning in source.warnings)


def test_wikidata_ignores_deprecated_and_ended_parent_claims(make_http):
    record = wd_record(
        claims={
            "P749": [
                statement({"id": "Q2"}, rank="deprecated"),
                statement(
                    {"id": "Q3"},
                    qualifiers={
                        "P582": [{"datavalue": {"value": {"time": "+2000-01-01T00:00:00Z"}}}]
                    },
                ),
                statement({"id": "Q4"}),
            ]
        }
    )

    def handle(request):
        if "query" in request.url.params:
            return httpx.Response(200, json={"results": {"bindings": []}})
        return httpx.Response(200, json={"entities": {"Q1": record}})

    neighbors = Wikidata(make_http(handle)).neighbors("Q1", "parents")
    assert [n.identifier for n in neighbors] == ["Q4"]


def test_gleif_uses_relationship_links_and_paginates(make_http):
    root = lei_record(
        relationships={
            "direct-parent": {"links": {"lei-record": "https://api.gleif.org/api/v1/parent"}},
            "ultimate-parent": {
                "links": {"reporting-exception": "https://api.gleif.org/api/v1/exception"}
            },
            "direct-children": {"links": {"related": "https://api.gleif.org/api/v1/children"}},
        }
    )

    def handle(request):
        path = request.url.path
        if path.endswith("/children"):
            return httpx.Response(
                200,
                json={
                    "data": [lei_record("A" * 20)],
                    "links": {"next": "https://api.gleif.org/api/v1/page2"},
                },
            )
        if path.endswith("/page2"):
            return httpx.Response(200, json={"data": [lei_record("B" * 20)]})
        if path.endswith("/parent"):
            return httpx.Response(200, json={"data": lei_record("C" * 20)})
        if path.endswith("/exception"):
            return httpx.Response(
                200, json={"data": {"attributes": {"exceptionReasons": ["NO_LEI"]}}}
            )
        return httpx.Response(200, json={"data": root})

    gleif = Gleif(make_http(handle))
    parents = gleif.neighbors(root["id"], "parents")
    assert [p.kind for p in parents] == ["accounting_direct_parent"]
    assert "reporting exception" in " ".join(gleif.warnings)
    assert len(gleif.neighbors(root["id"], "children")) == 2
    assert not [f for f in gleif.get(root["id"]).facts if f["kind"] == "operating_country"]


def test_gleif_rejects_untrusted_pagination_url(make_http):
    gleif = Gleif(
        make_http(
            lambda _: httpx.Response(
                200,
                json={"data": [lei_record()], "links": {"next": "https://unrelated.test/secret"}},
            )
        )
    )
    with pytest.raises(SourceError, match="GLEIF URL"):
        gleif.search("Example")


def test_wikidata_missing_entity_and_error_envelope_are_errors(make_http):
    wd = Wikidata(
        make_http(
            lambda _: httpx.Response(200, json={"entities": {"Q1": {"id": "Q1", "missing": ""}}})
        )
    )
    with pytest.raises(SourceError, match="Q1"):
        wd.get("Q1")


def test_wikidata_does_not_turn_generic_owners_into_companies(make_http):
    def handle(request):
        if "query" in request.url.params:
            return httpx.Response(200, json={"results": {"bindings": []}})
        return httpx.Response(200, json={"entities": {"Q1": wd_record("Q1", "Treasury stock")}})

    node = Wikidata(make_http(handle)).get("Q1")
    assert node.schema == "Thing"


def test_wikidata_soe_subclass_and_state_owner_are_source_evidence(make_http):
    def handle(request):
        if "query" in request.url.params:
            return httpx.Response(
                200,
                json={
                    "results": {
                        "bindings": [{"type": {"value": "http://www.wikidata.org/entity/Q270791"}}]
                    }
                },
            )
        identifiers = request.url.params["ids"].split("|")
        records = {
            "Q1": wd_record(claims={"P127": [statement({"id": "Q2"})]}),
            "Q2": wd_record("Q2", "Example State", {"P31": [statement({"id": "Q6256"})]}),
        }
        return httpx.Response(200, json={"entities": {q: records[q] for q in identifiers}})

    node = Wikidata(make_http(handle)).get("Q1")
    assert {f["kind"] for f in node.flags} == {"soe", "state_ownership"}
