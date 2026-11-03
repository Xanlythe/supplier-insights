import hashlib
import json
import os
import re
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import httpx
import typer
from dotenv import load_dotenv

from supplier_insights.graph import AmbiguousMatch, build_graph, select_candidate
from supplier_insights.http import USER_AGENT, CachedHttp, SourceError
from supplier_insights.screening import ScreeningIndex, validate_dataset
from supplier_insights.sources.gleif import Gleif, validate_lei
from supplier_insights.sources.wikidata import Wikidata, validate_qid

app = typer.Typer(no_args_is_help=True, help="Non-commercial supply-chain evidence explorer.")
DATA = "https://data.opensanctions.org/datasets/latest"


def data_dir() -> Path:
    load_dotenv(Path.cwd() / ".env")
    path = Path(os.environ.get("SUPPLIER_INSIGHTS_DATA_DIR", ".supplier-insights"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def http_client(path: Path) -> CachedHttp:
    return CachedHttp(
        path / "cache.sqlite",
        user_agent=os.environ.get("SUPPLIER_INSIGHTS_USER_AGENT", USER_AGENT),
    )


def fail(exc: Exception):
    typer.echo(f"Error: {exc}", err=True)
    raise typer.Exit(1) from exc


@app.command()
def lookup(
    name: Annotated[str, typer.Argument(help="Company name to search.")],
    depth: Annotated[int, typer.Option(min=0, max=5)] = 1,
    qid: Annotated[str | None, typer.Option(help="Explicit Wikidata QID.")] = None,
    lei: Annotated[str | None, typer.Option(help="Explicit GLEIF LEI.")] = None,
    max_nodes: Annotated[int, typer.Option(min=1, max=1000)] = 100,
    relation_limit: Annotated[int, typer.Option(min=1, max=1000)] = 100,
    tree: Annotated[bool, typer.Option("--tree/--no-tree")] = True,
):
    """Write JSON to stdout and an evidence tree to stderr."""
    try:
        if not name.strip():
            raise ValueError("Company name cannot be empty")
        if qid:
            validate_qid(qid)
        if lei:
            validate_lei(lei)
        path = data_dir()
        with http_client(path) as http, ScreeningIndex(path / "screening.sqlite") as index:
            wd, gleif = Wikidata(http, relation_limit), Gleif(http, relation_limit)
            warnings = []
            candidates = {}
            for source, explicit in ((wd, qid), (gleif, lei)):
                if explicit:
                    continue
                try:
                    candidates[source.name] = source.search(name)
                except SourceError as exc:
                    warnings.append(f"{source.name} search failed: {exc}")
            if not qid and wd.name in candidates:
                if lei:
                    try:
                        linked = wd.find_by_lei(lei)
                        if len(linked) == 1:
                            qid = linked[0]
                    except SourceError as exc:
                        warnings.append(f"Wikidata LEI join unavailable: {exc}")
                if not qid:
                    qid = select_candidate(candidates[wd.name], wd.name, name)
            if qid and not lei:
                try:
                    identifiers = wd.get(qid).identifiers.get("lei", [])
                    if len(identifiers) == 1:
                        lei = identifiers[0]
                except SourceError as exc:
                    warnings.append(f"Wikidata identifier enrichment failed: {exc}")
            if not lei and gleif.name in candidates:
                lei = select_candidate(candidates[gleif.name], gleif.name, name)
            if not qid and lei:
                try:
                    linked = wd.find_by_lei(lei)
                    if len(linked) == 1:
                        qid = linked[0]
                    elif len(linked) > 1:
                        warnings.append(
                            f"Multiple Wikidata QIDs for LEI {lei}: {linked}; not merged."
                        )
                except SourceError as exc:
                    warnings.append(f"Wikidata LEI join unavailable: {exc}")
            seeds = []
            if qid:
                seeds.append((wd, qid))
            if lei:
                seeds.append((gleif, lei))
            if not seeds:
                raise SourceError("No company found. " + "; ".join(warnings))
            graph = build_graph(
                seeds,
                depth=depth,
                max_nodes=max_nodes,
                additional_sources=[wd, gleif],
                resolve_lei=wd.find_by_lei,
            )
            graph.warnings.extend(warnings)
            for source in (wd, gleif):
                if not candidates.get(source.name) and not (qid if source.name == wd.name else lei):
                    graph.warnings.append(f"No {source.name} company selected.")
            graph.save(path / "graph.sqlite")
            report = graph.report()
            report.update(
                {
                    "query": name,
                    "generated_at": datetime.now(UTC).isoformat(),
                    "purpose": "Personal, non-commercial supply-chain research",
                    "limits": {
                        "depth": depth,
                        "max_nodes": max_nodes,
                        "relation_limit": relation_limit,
                    },
                    "candidates": candidates,
                    "requests": http.requests,
                    "screening": {key: index.screen(node) for key, node in graph.nodes.items()},
                }
            )
            if tree:
                typer.echo(graph.tree(), err=True)
                for node_id, node in graph.nodes.items():
                    typer.echo(f"\n{node.name} [{node_id}]", err=True)
                    for fact in node.facts:
                        typer.echo(f"  {fact['kind']}: {json.dumps(fact['value'])}", err=True)
                    if not any(f["kind"] == "operating_country" for f in node.facts):
                        typer.echo("  operating countries: unknown (not reported)", err=True)
                    for flag in node.flags:
                        typer.echo(f"  source flag: {flag['kind']} ({flag['source']})", err=True)
                    screening = report["screening"][node_id]
                    typer.echo(f"  OpenSanctions: {screening['status']}", err=True)
                    for match in screening["matches"]:
                        typer.echo(
                            f"    {match['entity']['id']}: {match['confidence']}; "
                            f"flags={match['flags']}",
                            err=True,
                        )
                        for connection in match["connections"]:
                            if connection["flags"]:
                                typer.echo(
                                    f"      connection: {connection['entity']['id']} "
                                    f"{connection['flags']}",
                                    err=True,
                                )
                    typer.echo(f"  {screening['warning']}", err=True)
                for warning in report["warnings"]:
                    typer.echo(f"Warning: {warning}", err=True)
            typer.echo(json.dumps(report, indent=2, ensure_ascii=True))
    except (AmbiguousMatch, SourceError, ValueError, OSError, sqlite3.Error) as exc:
        fail(exc)


@app.command("import-ftm")
def import_ftm(
    file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    dataset: Annotated[str, typer.Option(help="Collection ID for atomic replacement.")] = "default",
):
    """Index a local newline-delimited FtM snapshot without network access."""
    try:
        with ScreeningIndex(data_dir() / "screening.sqlite") as index:
            snapshot = index.import_file(file, dataset)
        typer.echo(json.dumps(snapshot, indent=2))
    except (ValueError, OSError, sqlite3.Error) as exc:
        fail(exc)


@app.command()
def sync(
    dataset: Annotated[str, typer.Option(help="OpenSanctions dataset/collection ID.")] = "default",
):
    """Explicitly download and transactionally replace a local FtM collection."""
    try:
        validate_dataset(dataset)
        path = data_dir()
        with http_client(path) as http, ScreeningIndex(path / "screening.sqlite") as index:
            row = index.db.execute(
                "SELECT metadata FROM snapshots WHERE dataset=?", (dataset,)
            ).fetchone()
            if row:
                imported = datetime.fromisoformat(json.loads(row[0])["imported_at"])
                if (datetime.now(UTC) - imported).total_seconds() < 21600:
                    raise ValueError("Snapshot imported less than six hours ago; sync later.")
            base = f"{DATA}/{dataset}"
            metadata = http.get_json(f"{base}/index.json", refresh=True)
            if metadata.get("name") != dataset:
                raise SourceError("OpenSanctions metadata dataset does not match the request")
            resource = next(
                (r for r in metadata.get("resources", []) if r.get("name") == "entities.ftm.json"),
                None,
            )
            if not resource or not isinstance(resource.get("url"), str):
                raise SourceError("OpenSanctions metadata has no FtM resource URL")
            url = resource["url"]
            parsed = httpx.URL(url)
            if parsed.scheme != "https" or parsed.host != "data.opensanctions.org":
                raise SourceError(f"Untrusted OpenSanctions resource URL: {url}")
            checksum = resource.get("checksum", "")
            if not isinstance(checksum, str) or not re.fullmatch(r"[a-fA-F0-9]{40}", checksum):
                raise SourceError("OpenSanctions resource has no valid SHA-1 checksum")
            typer.echo(f"Downloading {url}; this may be large. Non-commercial use only.", err=True)
            with tempfile.TemporaryDirectory(prefix="sync-", dir=path) as temporary:
                download = Path(temporary) / "entities.ftm.json"
                http.download(url, download)
                with download.open("rb") as stream:
                    actual = hashlib.file_digest(
                        stream, lambda: hashlib.sha1(usedforsecurity=False)
                    ).hexdigest()
                if actual != checksum.lower():
                    raise SourceError(
                        "OpenSanctions checksum mismatch; previous snapshot preserved"
                    )
                snapshot = index.import_file(download, dataset, metadata=metadata, source_url=url)
            typer.echo(json.dumps(snapshot, indent=2))
    except (SourceError, ValueError, OSError, sqlite3.Error) as exc:
        fail(exc)
