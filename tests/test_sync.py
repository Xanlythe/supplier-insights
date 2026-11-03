import hashlib
import json

import httpx
from typer.testing import CliRunner

from supplier_insights.cli import app
from supplier_insights.models import Node
from supplier_insights.screening import ScreeningIndex

runner = CliRunner()


def fixture_export():
    body = (
        json.dumps(
            {
                "id": "test-company",
                "schema": "Company",
                "properties": {"name": ["Example"], "wikidataId": ["Q1"]},
            }
        ).encode()
        + b"\n"
    )
    url = "https://data.opensanctions.org/artifacts/test/v1/entities.ftm.json"
    metadata = {
        "name": "test",
        "version": "v1",
        "resources": [
            {
                "name": "entities.ftm.json",
                "url": url,
                "size": len(body),
                "checksum": hashlib.sha1(body, usedforsecurity=False).hexdigest(),
            }
        ],
    }
    return body, url, metadata


def test_sync_downloads_immutable_resource_and_verifies_it(tmp_path, monkeypatch, make_http):
    body, url, metadata = fixture_export()
    calls = []

    def handle(request):
        calls.append(str(request.url))
        if request.url.path.endswith("index.json"):
            return httpx.Response(200, json=metadata)
        assert str(request.url) == url
        assert "SupplierInsights" in request.headers["User-Agent"]
        return httpx.Response(200, content=body)

    monkeypatch.setenv("SUPPLIER_INSIGHTS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("supplier_insights.cli.http_client", lambda _: make_http(handle))
    result = runner.invoke(app, ["sync", "--dataset", "test"])
    assert result.exit_code == 0, result.output
    snapshot = json.loads(result.stdout)
    assert snapshot["count"] == 1
    assert snapshot["source_url"] == url
    assert snapshot["metadata"]["version"] == "v1"
    assert not list(tmp_path.glob("sync-*"))
    assert runner.invoke(app, ["sync", "--dataset", "test"]).exit_code == 1
    assert len(calls) == 2


def test_checksum_failure_preserves_previous_snapshot(tmp_path, monkeypatch, make_http):
    body, _, metadata = fixture_export()
    metadata["resources"][0]["checksum"] = "0" * 40
    old = tmp_path / "old.json"
    old.write_bytes(body)
    with ScreeningIndex(tmp_path / "screening.sqlite") as index:
        index.import_file(old, "test")
        previous = json.loads(index.db.execute("SELECT metadata FROM snapshots").fetchone()[0])
        previous["imported_at"] = "2000-01-01T00:00:00+00:00"
        with index.db:
            index.db.execute("UPDATE snapshots SET metadata=?", (json.dumps(previous),))

    def handle(request):
        if request.url.path.endswith("index.json"):
            return httpx.Response(200, json=metadata)
        return httpx.Response(200, content=body)

    monkeypatch.setenv("SUPPLIER_INSIGHTS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("supplier_insights.cli.http_client", lambda _: make_http(handle))
    result = runner.invoke(app, ["sync", "--dataset", "test"])
    assert result.exit_code == 1
    assert "checksum" in result.output.lower()
    with ScreeningIndex(tmp_path / "screening.sqlite") as index:
        assert index.screen(Node("x", "Example", "Company", {}))["matches"]


def test_sync_rejects_foreign_resource_url(tmp_path, monkeypatch, make_http):
    _, _, metadata = fixture_export()
    metadata["resources"][0]["url"] = "https://unrelated.test/snapshot"
    monkeypatch.setenv("SUPPLIER_INSIGHTS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "supplier_insights.cli.http_client",
        lambda _: make_http(lambda request: httpx.Response(200, json=metadata)),
    )
    result = runner.invoke(app, ["sync", "--dataset", "test"])
    assert result.exit_code == 1
    assert "resource URL" in result.output
