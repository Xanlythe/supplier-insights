import json

from typer.testing import CliRunner

from supplier_insights.cli import app
from supplier_insights.models import Neighbor, Node

runner = CliRunner()


def test_cli_json_stdout_tree_stderr_and_not_screened(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPPLIER_INSIGHTS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "supplier_insights.sources.wikidata.Wikidata.search",
        lambda self, name: [{"id": "Q1", "name": "Example"}],
    )
    monkeypatch.setattr("supplier_insights.sources.gleif.Gleif.search", lambda self, name: [])
    monkeypatch.setattr(
        "supplier_insights.sources.wikidata.Wikidata.get",
        lambda self, qid: Node("wikidata:Q1", "Example", "Organization", {"qid": ["Q1"]}),
    )
    result = runner.invoke(app, ["lookup", "Example", "--depth", "0"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["roots"] == ["wikidata:Q1"]
    assert report["screening"]["wikidata:Q1"]["status"] == "not_performed"
    assert "Example" in result.stderr
    assert "not_performed" in result.stderr


def test_bad_identifiers_rejected_before_network(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPPLIER_INSIGHTS_DATA_DIR", str(tmp_path))
    result = runner.invoke(app, ["lookup", "Example", "--qid", "Q1} UNION {"])
    assert result.exit_code != 0
    assert "QID" in result.output


def test_invalid_depth_is_rejected():
    assert runner.invoke(app, ["lookup", "Example", "--depth", "-1"]).exit_code != 0


def test_import_cli_preserves_json_stdout(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPPLIER_INSIGHTS_DATA_DIR", str(tmp_path / "state"))
    path = tmp_path / "snapshot.json"
    path.write_text(
        json.dumps({"id": "c", "schema": "Company", "properties": {"name": ["Example"]}}),
        encoding="utf-8",
    )
    result = runner.invoke(app, ["import-ftm", str(path), "--dataset", "test"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["count"] == 1


def test_company_name_lookup_selects_exact_company_and_displays_subsidiaries(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPPLIER_INSIGHTS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "supplier_insights.sources.wikidata.Wikidata.search",
        lambda self, name: [
            {"id": "Q2283", "name": "Microsoft"},
            {"id": "Q135288", "name": "Microsoft Store"},
        ],
    )
    monkeypatch.setattr("supplier_insights.sources.gleif.Gleif.search", lambda self, name: [])
    names = {
        "Q2283": "Microsoft",
        "Q364": "GitHub",
        "Q127278673": "LinkedIn Corporation",
        "Q189557": "ZeniMax Media",
    }
    monkeypatch.setattr(
        "supplier_insights.sources.wikidata.Wikidata.get",
        lambda self, qid: Node(f"wikidata:{qid}", names[qid], "Company", {"qid": [qid]}),
    )
    monkeypatch.setattr(
        "supplier_insights.sources.wikidata.Wikidata.neighbors",
        lambda self, qid, direction: (
            [
                Neighbor(child, "parent_organization", "https://www.wikidata.org/wiki/Q2283")
                for child in ("Q364", "Q127278673", "Q189557")
            ]
            if qid == "Q2283" and direction == "children"
            else []
        ),
    )
    result = runner.invoke(app, ["lookup", "Microsoft"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["roots"] == ["wikidata:Q2283"]
    assert {node["name"] for node in report["nodes"].values()} == set(names.values())
    assert "GitHub" in result.stderr
    assert "LinkedIn Corporation" in result.stderr
    assert "ZeniMax Media" in result.stderr
    assert "Ambiguous" not in result.output
