"""Local, transactional FtM snapshots with conservative identity matching."""

import hashlib
import json
import re
import sqlite3
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from followthemoney import model

from supplier_insights.models import Node

DISCLAIMER = (
    "No local match is not clearance. Review source evidence, snapshot age, collection coverage, "
    "historical dates, and identity confidence. Name candidates are not confirmed identities."
)


def validate_dataset(dataset: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", dataset):
        raise ValueError(
            "Dataset must contain only lowercase letters, digits, underscores or hyphens"
        )
    return dataset


def normalize_name(name: str) -> str:
    text = unicodedata.normalize("NFKC", name).casefold()
    return " ".join(re.sub(r"[^\w\s]", " ", text).split())


def record_flags(record: dict[str, Any]) -> list[str]:
    topics = set(record["properties"].get("topics", []))
    flags = []
    if any(t == "role.pep" or t.startswith("role.pep.") for t in topics):
        flags.append("pep")
    if topics & {"gov.soe", "role.soe"}:
        flags.append("soe")
    if "gov.owned" in topics:
        flags.append("state_ownership")
    if topics & {"debarment", "crime.debarment"} or any(
        "worldbank_debarred" in dataset for dataset in record.get("datasets", [])
    ):
        flags.append("debarment")
    if "sanction" in topics:
        flags.append("sanction")
    return flags


class ScreeningIndex:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS snapshots (dataset TEXT PRIMARY KEY, metadata TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS entities (
                dataset TEXT, id TEXT, body TEXT NOT NULL, PRIMARY KEY(dataset, id));
            CREATE TABLE IF NOT EXISTS terms (dataset TEXT, entity TEXT, kind TEXT, value TEXT);
            CREATE INDEX IF NOT EXISTS terms_lookup ON terms(kind, value);
            CREATE TABLE IF NOT EXISTS refs (dataset TEXT, entity TEXT, property TEXT, target TEXT);
            CREATE INDEX IF NOT EXISTS refs_target ON refs(dataset, target);
            CREATE INDEX IF NOT EXISTS refs_entity ON refs(dataset, entity);
            CREATE TABLE IF NOT EXISTS aliases (
                dataset TEXT, alias TEXT, entity TEXT, PRIMARY KEY(dataset, alias, entity));
        """)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.db.close()

    def import_file(
        self,
        path: Path,
        dataset: str,
        *,
        metadata: dict[str, Any] | None = None,
        source_url: str | None = None,
    ) -> dict[str, Any]:
        validate_dataset(dataset)
        digest, count = hashlib.sha256(), 0
        with self.db:
            for table in ("entities", "terms", "refs", "aliases"):
                self.db.execute(f"DELETE FROM {table} WHERE dataset=?", (dataset,))
            with path.open("rb") as stream:
                for line_number, raw in enumerate(stream, 1):
                    digest.update(raw)
                    if not raw.strip():
                        continue
                    try:
                        record = json.loads(raw)
                        self._insert(dataset, record)
                    except (ValueError, TypeError, KeyError, sqlite3.IntegrityError) as exc:
                        raise ValueError(f"Invalid FtM at line {line_number}: {exc}") from exc
                    count += 1
            if not count:
                raise ValueError("Empty FtM snapshot; previous collection was preserved")
            snapshot = {
                "dataset": dataset,
                "imported_at": datetime.now(UTC).isoformat(),
                "sha256": digest.hexdigest(),
                "count": count,
                "source_url": source_url,
                "metadata": metadata,
                "license_note": "Non-commercial only; review OpenSanctions and upstream terms.",
            }
            self.db.execute(
                "INSERT OR REPLACE INTO snapshots VALUES (?, ?)", (dataset, json.dumps(snapshot))
            )
        return snapshot

    def _insert(self, dataset: str, record: dict[str, Any]):
        if (
            not isinstance(record, dict)
            or not isinstance(record.get("id"), str)
            or not record["id"]
        ):
            raise ValueError("Entity requires a non-empty id")
        schema = model.get(record.get("schema"))
        if schema is None:
            raise ValueError(f"Unsupported FtM schema: {record.get('schema')}; review FtM version")
        properties = record.get("properties")
        if not isinstance(properties, dict) or any(
            not isinstance(vals, list) or not all(isinstance(value, str) for value in vals)
            for vals in properties.values()
        ):
            raise ValueError("properties must map property names to lists of strings")
        for field in ("datasets", "referents"):
            if field in record and (
                not isinstance(record[field], list)
                or not all(isinstance(v, str) for v in record[field])
            ):
                raise ValueError(f"{field} must be a list of strings")
        identifier = record["id"]
        self.db.execute(
            "INSERT INTO entities VALUES (?, ?, ?)", (dataset, identifier, json.dumps(record))
        )
        for alias in [identifier, *record.get("referents", [])]:
            existing = self.db.execute(
                "SELECT entity FROM aliases WHERE dataset=? AND alias=?",
                (dataset, alias),
            ).fetchone()
            if existing and existing[0] != identifier:
                raise ValueError(f"Ambiguous referent {alias}: {existing[0]} and {identifier}")
            self.db.execute(
                "INSERT OR IGNORE INTO aliases VALUES (?, ?, ?)", (dataset, alias, identifier)
            )
        for prop, vals in properties.items():
            kind = {
                "leiCode": "lei",
                "wikidataId": "qid",
                "name": "name",
                "alias": "name",
                "previousName": "name",
            }.get(prop)
            if kind:
                self.db.executemany(
                    "INSERT INTO terms VALUES (?, ?, ?, ?)",
                    [
                        (
                            dataset,
                            identifier,
                            kind,
                            normalize_name(value) if kind == "name" else value,
                        )
                        for value in vals
                    ],
                )
            schema_prop = schema.properties.get(prop)
            if schema_prop and schema_prop.type.name == "entity":
                self.db.executemany(
                    "INSERT INTO refs VALUES (?, ?, ?, ?)",
                    [(dataset, identifier, prop, value) for value in vals],
                )

    def _entity(self, dataset: str, identifier: str) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT e.body FROM entities e JOIN aliases a "
            "ON e.dataset=a.dataset AND e.id=a.entity WHERE a.dataset=? AND a.alias=? LIMIT 1",
            (dataset, identifier),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def _connections(self, dataset: str, identifier: str) -> tuple[list[dict[str, Any]], bool]:
        aliases = [
            r[0]
            for r in self.db.execute(
                "SELECT alias FROM aliases WHERE dataset=? AND entity=?", (dataset, identifier)
            )
        ]
        refs = set()
        for alias in aliases:
            refs.update(
                self.db.execute(
                    "SELECT entity, property, target FROM refs WHERE dataset=? AND target=?",
                    (dataset, alias),
                )
            )
        refs.update(
            self.db.execute(
                "SELECT entity, property, target FROM refs WHERE dataset=? AND entity=?",
                (dataset, identifier),
            )
        )
        connections = []
        seen = set()
        for origin, prop, target in sorted(refs):
            context_id = target if origin == identifier else origin
            context = self._entity(dataset, context_id)
            if not context:
                continue
            schema = model.get(context["schema"])
            endpoints = [context]
            if schema.is_a("Interval"):
                for endpoint_prop, vals in context["properties"].items():
                    definition = schema.properties.get(endpoint_prop)
                    if definition and definition.type.name == "entity":
                        endpoints.extend(
                            entity
                            for value in vals
                            if (entity := self._entity(dataset, value))
                            and entity["id"] != identifier
                        )
            for endpoint in endpoints:
                key = (context["id"], endpoint["id"])
                if key in seen:
                    continue
                seen.add(key)
                connections.append(
                    {
                        "entity": endpoint,
                        "relationship": context,
                        "via_property": prop,
                        "flags": record_flags(endpoint),
                        "source": f"https://www.opensanctions.org/entities/{endpoint['id']}/",
                    }
                )
        # Put flagged connected entities first, retaining the actual relationship evidence.
        connections.sort(key=lambda c: (not bool(c["flags"]), c["entity"]["id"]))
        return connections[:100], len(connections) > 100

    def screen(self, node: Node) -> dict[str, Any]:
        snapshots = [
            json.loads(row[0])
            for row in self.db.execute("SELECT metadata FROM snapshots ORDER BY dataset")
        ]
        if not snapshots:
            return {
                "status": "not_performed",
                "matches": [],
                "snapshots": [],
                "warning": "No OpenSanctions snapshot imported. " + DISCLAIMER,
            }
        candidates: dict[tuple[str, str], int] = {}
        terms = [(key, value) for key, vals in node.identifiers.items() for value in vals]
        terms.extend(("name", normalize_name(name)) for name in [node.name, *node.aliases])
        truncated = False
        for kind, value in terms:
            rows = self.db.execute(
                "SELECT DISTINCT dataset, entity FROM terms WHERE kind=? AND value=? "
                "ORDER BY dataset, entity LIMIT 51",
                (kind, value),
            ).fetchall()
            truncated = truncated or len(rows) > 50
            for row in rows:
                candidates[row] = min(candidates.get(row, 1), 1 if kind == "name" else 0)
        matches = []
        truncated = truncated or len(candidates) > 50
        ordered = sorted(candidates, key=lambda key: (candidates[key], key))
        for dataset, identifier in ordered[:50]:
            record = self._entity(dataset, identifier)
            if not record:
                continue
            props = record["properties"]
            ids = {"qid": props.get("wikidataId", []), "lei": props.get("leiCode", [])}
            conflict = any(
                node.identifiers.get(key) and vals and not set(vals) & set(node.identifiers[key])
                for key, vals in ids.items()
            )
            exact = any(set(vals) & set(node.identifiers.get(key, [])) for key, vals in ids.items())
            confidence = (
                "identifier_conflict"
                if conflict
                else ("identifier" if exact else "unverified_name")
            )
            connections, context_truncated = self._connections(dataset, identifier)
            matches.append(
                {
                    "entity": record,
                    "collection": dataset,
                    "confidence": confidence,
                    "flags": record_flags(record),
                    "connections": connections,
                    "connections_truncated": context_truncated,
                    "source": f"https://www.opensanctions.org/entities/{identifier}/",
                }
            )
        return {
            "status": "screened",
            "matches": matches,
            "snapshots": snapshots,
            "matches_truncated": truncated,
            "warning": DISCLAIMER,
        }
