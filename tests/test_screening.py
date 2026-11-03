import json

import pytest

from supplier_insights.models import Node
from supplier_insights.screening import ScreeningIndex


def entity(identifier, schema, **properties):
    return {
        "id": identifier,
        "schema": schema,
        "properties": properties,
        "datasets": ["synthetic_test"],
        "referents": [],
    }


def write_snapshot(path, records):
    path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    return path


def test_screening_joins_identifiers_and_flags_name_candidates_with_context(tmp_path):
    path = write_snapshot(
        tmp_path / "data.json",
        [
            entity("company", "Company", name=["Example"], wikidataId=["Q1"]),
            entity("person", "Person", name=["A Person"], topics=["role.pep"]),
            entity("director", "Directorship", director=["person"], organization=["company"]),
            entity("other", "Company", name=["Example"], topics=["debarment"]),
            entity("position", "Position", name=["Minister"]),
            entity("hq", "Address", full=["Somewhere"]),
        ],
    )
    with ScreeningIndex(tmp_path / "index.sqlite") as index:
        index.import_file(path, "test")
        result = index.screen(Node("wd:Q1", "Example", "Organization", {"qid": ["Q1"]}))
    matches = {m["entity"]["id"]: m for m in result["matches"]}
    assert matches["company"]["confidence"] == "identifier"
    assert matches["other"]["confidence"] == "unverified_name"
    assert "debarment" in matches["other"]["flags"]
    context = matches["company"]["connections"]
    assert context[0]["entity"]["id"] == "person"
    assert "pep" in context[0]["flags"]
    assert result["snapshots"][0]["sha256"]


def test_import_is_atomic_and_replaces_removed_entities(tmp_path):
    path = tmp_path / "data.json"
    with ScreeningIndex(tmp_path / "index.sqlite") as index:
        index.import_file(write_snapshot(path, [entity("old", "Company", name=["Old"])]), "test")
        path.write_text('{"bad": true}\n', encoding="utf-8")
        with pytest.raises(ValueError, match="line 1"):
            index.import_file(path, "test")
        assert index.screen(Node("x", "Old", "Company", {}))["matches"]
        index.import_file(write_snapshot(path, [entity("new", "Company", name=["New"])]), "test")
        assert index.screen(Node("x", "Old", "Company", {}))["matches"] == []


def test_unscreened_is_not_clearance(tmp_path):
    with ScreeningIndex(tmp_path / "index.sqlite") as index:
        result = index.screen(Node("x", "Example", "Company", {}))
    assert result["status"] == "not_performed"
    assert result["warning"]


def test_identifier_conflict_is_flagged_even_when_name_matches(tmp_path):
    path = write_snapshot(
        tmp_path / "data.json", [entity("other", "Company", name=["Example"], wikidataId=["Q2"])]
    )
    with ScreeningIndex(tmp_path / "index.sqlite") as index:
        index.import_file(path, "test")
        match = index.screen(Node("x", "Example", "Company", {"qid": ["Q1"]}))["matches"][0]
    assert match["confidence"] == "identifier_conflict"


def test_referents_resolve_relationships(tmp_path):
    company = entity("company", "Company", name=["Example"], wikidataId=["Q1"])
    company["referents"] = ["old-company-id"]
    path = write_snapshot(
        tmp_path / "data.json",
        [
            company,
            entity("person", "Person", name=["Owner"], topics=["role.pep"]),
            entity("own", "Ownership", owner=["person"], asset=["old-company-id"]),
        ],
    )
    with ScreeningIndex(tmp_path / "index.sqlite") as index:
        index.import_file(path, "test")
        result = index.screen(Node("x", "Example", "Company", {"qid": ["Q1"]}))
    assert result["matches"][0]["connections"][0]["entity"]["id"] == "person"


def test_identifier_match_is_not_lost_among_many_name_candidates(tmp_path):
    records = [entity(f"a{i:03}", "Company", name=["Example"]) for i in range(60)]
    records.append(entity("z-exact", "Company", name=["Other"], wikidataId=["Q1"]))
    path = write_snapshot(tmp_path / "data.json", records)
    with ScreeningIndex(tmp_path / "index.sqlite") as index:
        index.import_file(path, "test")
        result = index.screen(Node("x", "Example", "Company", {"qid": ["Q1"]}))
    assert result["matches"][0]["entity"]["id"] == "z-exact"
    assert result["matches_truncated"]


def test_original_legal_name_is_screened_after_merge(tmp_path):
    path = write_snapshot(
        tmp_path / "data.json",
        [entity("company", "Company", name=["Example Legal Name"], topics=["gov.soe"])],
    )
    with ScreeningIndex(tmp_path / "index.sqlite") as index:
        index.import_file(path, "test")
        node = Node("x", "Example", "Company", {}, aliases=["Example Legal Name"])
        match = index.screen(node)["matches"][0]
    assert match["flags"] == ["soe"]


def test_ambiguous_referent_does_not_silently_select_an_entity(tmp_path):
    a = entity("a", "Company", name=["First"])
    b = entity("b", "Company", name=["Second"])
    a["referents"] = ["old-id"]
    b["referents"] = ["old-id"]
    path = write_snapshot(tmp_path / "data.json", [a, b])
    with ScreeningIndex(tmp_path / "index.sqlite") as index:
        with pytest.raises(ValueError, match="referent"):
            index.import_file(path, "test")
