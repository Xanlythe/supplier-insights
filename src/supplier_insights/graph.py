import copy
import json
import sqlite3
from collections import deque
from collections.abc import Callable
from contextlib import closing
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from supplier_insights.http import SourceError
from supplier_insights.models import Edge, Neighbor, Node


class Source(Protocol):
    name: str
    warnings: list[str]

    def get(self, identifier: str) -> Node: ...
    def neighbors(self, identifier: str, direction: str) -> list[Neighbor]: ...


class AmbiguousMatch(ValueError):
    pass


def select_candidate(
    candidates: list[dict[str, str]], source: str, query: str | None = None
) -> str | None:
    if query is not None:
        normalized = " ".join(query.casefold().split())
        exact = [
            candidate
            for candidate in candidates
            if " ".join(candidate["name"].casefold().split()) == normalized
        ]
        if len(exact) == 1:
            return exact[0]["id"]
    if len(candidates) > 1:
        raise AmbiguousMatch(
            "No company tree generated yet. "
            f"Ambiguous {source} organization search; "
            f"select --{'qid' if source == 'wikidata' else 'lei'}:\n"
            + json.dumps(candidates, ensure_ascii=True, indent=2)
        )
    return candidates[0]["id"] if candidates else None


def shared_ids(a: Node, b: Node) -> bool:
    return any(set(vals) & set(b.identifiers.get(key, [])) for key, vals in a.identifiers.items())


def conflicting_ids(a: Node, b: Node) -> bool:
    return any(
        vals and b.identifiers.get(key) and not set(vals) & set(b.identifiers[key])
        for key, vals in a.identifiers.items()
    )


@dataclass
class Graph:
    nodes: dict[str, Node] = field(default_factory=dict)
    roots: list[str] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    aliases: dict[str, str] = field(default_factory=dict)

    def canonical(self, identifier: str) -> str:
        while identifier in self.aliases:
            identifier = self.aliases[identifier]
        return identifier

    def add(self, incoming: Node) -> str:
        node = copy.deepcopy(incoming)
        matches = []
        for current in self.nodes.values():
            if not shared_ids(current, node):
                continue
            if conflicting_ids(current, node):
                self.warnings.append(
                    f"Identifier conflict: {current.id} and {node.id} kept separate."
                )
            else:
                matches.append(current.id)
        if not matches:
            self.nodes[node.id] = node
            return node.id
        target = self.nodes[matches[0]]
        for other in [node, *(self.nodes[mid] for mid in matches[1:])]:
            if conflicting_ids(target, other):
                self.warnings.append(f"Identifier conflict: refusing merge with {other.id}.")
                continue
            for key, vals in other.identifiers.items():
                target.identifiers[key] = sorted(set(target.identifiers.get(key, []) + vals))
            for field_name in ("facts", "sources", "flags", "aliases"):
                current = getattr(target, field_name)
                current.extend(
                    value for value in getattr(other, field_name) if value not in current
                )
            if other.name != target.name and other.name not in target.aliases:
                target.aliases.append(other.name)
            if target.schema == "Organization" and other.schema == "Company":
                target.schema = "Company"
            if other.id != target.id:
                self.aliases[other.id] = target.id
                self.nodes.pop(other.id, None)
        return target.id

    def finish(self):
        self.roots = list(dict.fromkeys(self.canonical(root) for root in self.roots))
        self.edges = list(
            dict.fromkeys(
                Edge(self.canonical(e.parent), self.canonical(e.child), e.kind, e.source)
                for e in self.edges
            )
        )
        self.warnings = list(dict.fromkeys(self.warnings))

    def report(self) -> dict[str, Any]:
        ftm = [entity for node in self.nodes.values() for entity in node.ftm()]
        ftm.extend(entity for edge in self.edges if (entity := edge.ftm()) is not None)
        return {
            "roots": self.roots,
            "nodes": {key: asdict(value) for key, value in self.nodes.items()},
            "edges": [asdict(edge) for edge in self.edges],
            "ftm_entities": ftm,
            "warnings": self.warnings,
            "location_coverage": {
                key: {
                    kind: "reported"
                    if any(f["kind"] == kind for f in node.facts)
                    else "not_reported"
                    for kind in ("headquarters", "registration_country", "operating_country")
                }
                for key, node in self.nodes.items()
            },
        }

    def save(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("PRAGMA foreign_keys=ON")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS nodes (id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS roots (id TEXT PRIMARY KEY REFERENCES nodes(id));
                CREATE TABLE IF NOT EXISTS edges (
                    parent TEXT REFERENCES nodes(id), child TEXT REFERENCES nodes(id),
                    kind TEXT, source TEXT, PRIMARY KEY(parent, child, kind, source));
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)
            for table in ("roots", "edges", "nodes"):
                db.execute(f"DELETE FROM {table}")
            db.executemany(
                "INSERT INTO nodes VALUES (?, ?)",
                [(key, json.dumps(asdict(node))) for key, node in self.nodes.items()],
            )
            db.executemany("INSERT INTO roots VALUES (?)", [(key,) for key in self.roots])
            db.executemany(
                "INSERT INTO edges VALUES (?, ?, ?, ?)",
                [(edge.parent, edge.child, edge.kind, edge.source) for edge in self.edges],
            )
            db.execute(
                "INSERT OR REPLACE INTO metadata VALUES ('warnings', ?)",
                (json.dumps(self.warnings),),
            )

    def tree(self) -> str:
        lines = []
        for root in self.roots:
            lines.append(f"{self.nodes[root].name} [{root}]")
            for direction in ("parents", "children"):
                lines.append(f"  {direction}:")

                def walk(current: str, indent: int, seen: set[str], direction=direction):
                    edges = [
                        e
                        for e in self.edges
                        if (e.child == current if direction == "parents" else e.parent == current)
                    ]
                    for edge in edges:
                        other = edge.parent if direction == "parents" else edge.child
                        cycle = other in seen
                        lines.append(
                            " " * indent + f"- {self.nodes[other].name} [{other}] "
                            f"({edge.kind})" + (" [cycle/reference]" if cycle else "")
                        )
                        if not cycle:
                            seen.add(other)
                            walk(other, indent + 2, seen)

                walk(root, 4, {root})
        return "\n".join(lines)


def build_graph(
    seeds: list[tuple[Source, str]],
    *,
    depth: int = 1,
    max_nodes: int = 100,
    additional_sources: list[Source] | None = None,
    resolve_lei: Callable[[str], list[str]] | None = None,
) -> Graph:
    if not 0 <= depth <= 5 or max_nodes < 1:
        raise ValueError("depth must be 0-5 and max_nodes must be positive")
    graph = Graph()
    routes: dict[tuple[int, str], str | None] = {}
    sources = {source.name: source for source in (additional_sources or [])}
    sources.update({source.name: source for source, _ in seeds})
    loaded: dict[tuple[int, str], Node] = {}
    resolved_leis: dict[str, list[str]] = {}

    def load(source: Source, identifier: str) -> str | None:
        key = (id(source), identifier)
        if key in routes:
            return routes[key]
        if len(graph.nodes) >= max_nodes:
            kind = {"gleif": "lei", "wikidata": "qid"}.get(source.name)
            if not any(identifier in n.identifiers.get(kind, []) for n in graph.nodes.values()):
                graph.warnings.append("max-nodes reached; corporate graph is partial.")
                return None
        try:
            node = source.get(identifier)
        except SourceError as exc:
            graph.warnings.append(f"{source.name} {identifier}: {exc}")
            routes[key] = None
            return None
        loaded[key] = node
        if len(graph.nodes) >= max_nodes and not any(
            shared_ids(n, node) and not conflicting_ids(n, node) for n in graph.nodes.values()
        ):
            graph.warnings.append("max-nodes reached; conflicting identity not added.")
            return None
        result = graph.add(node)
        routes[key] = result
        return result

    for source, identifier in seeds:
        root = load(source, identifier)
        if root:
            graph.roots.append(root)
    if not graph.roots:
        raise SourceError("No company could be loaded. " + "; ".join(graph.warnings))
    if len(set(graph.roots)) > 1:
        graph.warnings.append(
            "Selected records lack a consistent shared identifier; identities kept separate."
        )
    queue = deque(
        (source, identifier, direction, 0)
        for source, identifier in seeds
        for direction in ("parents", "children")
    )
    visited: dict[tuple[int, str, str], int] = {}
    while queue:
        source, identifier, direction, level = queue.popleft()
        key = (id(source), identifier, direction)
        if key in visited and visited[key] <= level:
            continue
        visited[key] = level
        current = load(source, identifier)
        if current is None:
            continue
        node = loaded[(id(source), identifier)]
        if source.name == "wikidata" and "gleif" in sources:
            for lei in node.identifiers.get("lei", []):
                queue.append((sources["gleif"], lei, direction, level))
        if source.name == "gleif" and "wikidata" in sources and resolve_lei:
            canonical_node = graph.nodes[graph.canonical(current)]
            for lei in node.identifiers.get("lei", []):
                if canonical_node.identifiers.get("qid"):
                    for qid in canonical_node.identifiers["qid"]:
                        queue.append((sources["wikidata"], qid, direction, level))
                    continue
                if lei not in resolved_leis:
                    try:
                        resolved_leis[lei] = resolve_lei(lei)
                    except SourceError as exc:
                        graph.warnings.append(f"Wikidata LEI join unavailable for {lei}: {exc}")
                        resolved_leis[lei] = []
                matches = resolved_leis[lei]
                if len(matches) == 1:
                    queue.append((sources["wikidata"], matches[0], direction, level))
                elif len(matches) > 1:
                    graph.warnings.append(f"Ambiguous QID join for LEI {lei}: {matches}.")
        if level >= depth or node.schema in ("Person", "Thing"):
            continue
        try:
            neighbors = source.neighbors(identifier, direction)
        except SourceError as exc:
            graph.warnings.append(f"{source.name} {identifier} {direction}: {exc}")
            continue
        for neighbor in neighbors:
            other = load(source, neighbor.identifier)
            if other:
                parent, child = (other, current) if direction == "parents" else (current, other)
                graph.edges.append(Edge(parent, child, neighbor.kind, neighbor.source))
                queue.append((source, neighbor.identifier, direction, level + 1))
    graph.warnings.append(
        f"Traversal depth={depth}; only documented relationships within limits are shown."
    )
    for source in sources.values():
        graph.warnings.extend(source.warnings)
    graph.finish()
    return graph
