import re
from datetime import UTC, datetime
from typing import Any

from supplier_insights.http import CachedHttp, SourceError
from supplier_insights.models import Neighbor, Node

API = "https://www.wikidata.org/w/api.php"
SPARQL = "https://query.wikidata.org/sparql"
PREFIXES = """
PREFIX wd: <http://www.wikidata.org/entity/>
PREFIX wdt: <http://www.wikidata.org/prop/direct/>
PREFIX p: <http://www.wikidata.org/prop/>
PREFIX ps: <http://www.wikidata.org/prop/statement/>
PREFIX pq: <http://www.wikidata.org/prop/qualifier/>
PREFIX wikibase: <http://wikiba.se/ontology#>
"""


def validate_qid(qid: str) -> str:
    if not re.fullmatch(r"Q[1-9][0-9]*", qid):
        raise ValueError(f"Invalid Wikidata QID: {qid}")
    return qid


def values(record: dict[str, Any], prop: str) -> list[Any]:
    result = []
    for claim in record.get("claims", {}).get(prop, []):
        if claim.get("rank") == "deprecated":
            continue
        ends = claim.get("qualifiers", {}).get("P582", [])
        if any(
            end.get("datavalue", {}).get("value", {}).get("time", "")[1:11]
            <= datetime.now(UTC).date().isoformat()
            for end in ends
            if end.get("datavalue", {}).get("value", {}).get("time", "").startswith("+")
        ):
            continue
        value = claim.get("mainsnak", {}).get("datavalue", {}).get("value")
        if value is not None:
            result.append(value)
    return result


def qids(record: dict[str, Any], prop: str) -> list[str]:
    return [v["id"] for v in values(record, prop) if isinstance(v, dict) and "id" in v]


class Wikidata:
    name = "wikidata"

    def __init__(self, http: CachedHttp, relation_limit: int = 100):
        self.http, self.limit = http, relation_limit
        self.records: dict[str, dict[str, Any]] = {}
        self.types: dict[str, set[str]] = {}
        self.nodes: dict[str, Node] = {}
        self.warnings: list[str] = []

    def search(self, name: str) -> list[dict[str, str]]:
        data = self.http.get_json(
            API,
            {
                "action": "wbsearchentities",
                "search": name,
                "language": "en",
                "type": "item",
                "limit": 10,
                "format": "json",
            },
        )
        if "search" not in data:
            raise SourceError("Wikidata returned no search field")
        if "search-continue" in data:
            self.warnings.append(
                "Wikidata search limited to 10 name matches before organization filtering; "
                "use --qid to select beyond these matches."
            )
        organizations = self._organization_ids([hit["id"] for hit in data["search"]])
        return [
            {
                "id": hit["id"],
                "name": hit.get("label", hit["id"]),
                "description": hit.get("description", ""),
            }
            for hit in data["search"]
            if hit["id"] in organizations
        ]

    def _organization_ids(self, identifiers: list[str]) -> set[str]:
        identifiers = list(dict.fromkeys(validate_qid(qid) for qid in identifiers))
        organizations = set()
        for start in range(0, len(identifiers), 50):
            batch = identifiers[start : start + 50]
            query = PREFIXES + (
                "SELECT DISTINCT ?item WHERE { VALUES ?item { "
                + " ".join(f"wd:{qid}" for qid in batch)
                + " } ?item wdt:P31/wdt:P279* wd:Q43229 . }"
                + f" LIMIT {len(batch) + 1}"
            )
            organizations.update(
                row["item"]["value"].rsplit("/", 1)[-1] for row in self._query(query)
            )
        return organizations

    def _load(self, identifiers: list[str]):
        missing = list(dict.fromkeys(q for q in identifiers if q not in self.records))
        for start in range(0, len(missing), 50):
            batch = missing[start : start + 50]
            for qid in batch:
                validate_qid(qid)
            data = self.http.get_json(
                API,
                {
                    "action": "wbgetentities",
                    "ids": "|".join(batch),
                    "props": "labels|descriptions|claims|aliases",
                    "languages": "en",
                    "format": "json",
                },
            )
            if not isinstance(data.get("entities"), dict):
                raise SourceError("Wikidata returned no entities")
            for qid in batch:
                record = data["entities"].get(qid)
                if not record or "missing" in record:
                    raise SourceError(f"Wikidata entity missing: {qid}")
                self.records[qid] = record

    def get(self, identifier: str) -> Node:
        validate_qid(identifier)
        if identifier in self.nodes:
            return self.nodes[identifier]
        self._load([identifier])
        record = self.records[identifier]
        source = f"https://www.wikidata.org/wiki/{identifier}"
        ids = {"qid": [identifier]}
        leis = [v for v in values(record, "P1278") if isinstance(v, str)]
        if leis:
            ids["lei"] = leis
        types = self._types(identifier)
        schema = "Thing"
        if "Q5" in types:
            schema = "Person"
        elif leis or types & {"Q783794", "Q4830453"}:
            schema = "Company"
        elif types & {"Q43229", "Q270791", "Q7188"}:
            schema = "Organization"
        node = Node(
            f"wikidata:{identifier}",
            record.get("labels", {}).get("en", {}).get("value", identifier),
            schema,
            ids,
            sources=[source],
        )
        locations = {prop: qids(record, prop) for prop in ("P159", "P17", "P2541")}
        self._load([qid for group in locations.values() for qid in group])
        hq_countries = [
            country for qid in locations["P159"] for country in qids(self.records[qid], "P17")
        ]
        self._load(hq_countries)
        for prop, identifiers in locations.items():
            for qid in identifiers:
                location = self.records[qid]
                label = location.get("labels", {}).get("en", {}).get("value", qid)
                codes = values(location, "P297")
                kind = {
                    "P159": "headquarters",
                    "P17": "associated_country",
                    "P2541": "operating_area",
                }[prop]
                value = {"full": label, "qid": qid}
                if codes:
                    value["country"] = codes[0]
                elif prop == "P159":
                    countries = [
                        code
                        for country in qids(location, "P17")
                        for code in values(self.records[country], "P297")
                    ]
                    if len(set(countries)) == 1:
                        value["country"] = countries[0]
                node.facts.append(
                    {"kind": kind, "value": value, "source": source, "property": prop}
                )
                if prop == "P2541" and codes:
                    node.facts.append(
                        {
                            "kind": "operating_country",
                            "value": {"country": codes[0], "qid": qid},
                            "source": source,
                            "property": prop,
                        }
                    )
        if "Q270791" in types:
            node.flags.append(
                {"kind": "soe", "source": source, "basis": "P31/P279* includes Q270791"}
            )
        owners = qids(record, "P127")
        if len(owners) > self.limit:
            self.warnings.append(f"State-owner classification limited for {identifier}.")
        self._load(owners[: self.limit])
        for owner in owners[: self.limit]:
            if self._types(owner) & {"Q6256", "Q7188", "Q3624078"}:
                node.flags.append(
                    {
                        "kind": "state_ownership",
                        "source": source,
                        "owner_qid": owner,
                        "basis": "P127 owner is a country, sovereign state or government",
                    }
                )
        node.aliases = [
            alias["value"]
            for alias in record.get("aliases", {}).get("en", [])
            if alias.get("value")
        ]
        self.nodes[identifier] = node
        return node

    def _types(self, identifier: str) -> set[str]:
        if identifier not in self.types:
            self._load([identifier])
            types = set(qids(self.records[identifier], "P31"))
            query = (
                PREFIXES
                + f"""SELECT DISTINCT ?type WHERE {{
                wd:{identifier} wdt:P31/wdt:P279* ?type .
                VALUES ?type {{ wd:Q5 wd:Q43229 wd:Q4830453 wd:Q783794
                    wd:Q270791 wd:Q6256 wd:Q7188 wd:Q3624078 }}
            }} LIMIT 10"""
            )
            try:
                types.update(row["type"]["value"].rsplit("/", 1)[-1] for row in self._query(query))
            except SourceError as exc:
                self.warnings.append(
                    f"Wikidata type classification unavailable for {identifier}: {exc}"
                )
            self.types[identifier] = types
        return self.types[identifier]

    def find_by_lei(self, lei: str) -> list[str]:
        if not re.fullmatch(r"[A-Z0-9]{20}", lei):
            raise ValueError("Invalid LEI")
        query = PREFIXES + f'SELECT ?item WHERE {{ ?item wdt:P1278 "{lei}" }} LIMIT 2'
        return [row["item"]["value"].rsplit("/", 1)[-1] for row in self._query(query)]

    def _query(self, query: str) -> list[dict[str, Any]]:
        data = self.http.get_json(SPARQL, {"query": query, "format": "json"})
        if not isinstance(data.get("results", {}).get("bindings"), list):
            raise SourceError("Invalid Wikidata SPARQL results")
        return data["results"]["bindings"]

    def neighbors(self, identifier: str, direction: str) -> list[Neighbor]:
        validate_qid(identifier)
        self._load([identifier])
        source = f"https://www.wikidata.org/wiki/{identifier}"
        record = self.records[identifier]
        pairs = (
            [("P749", "parent_organization"), ("P127", "ownership")]
            if direction == "parents"
            else [("P355", "parent_organization")]
        )
        found = [Neighbor(qid, kind, source) for prop, kind in pairs for qid in qids(record, prop)]
        # Reverse statements catch relationships recorded on only one side.
        reverse_props = ["P355"] if direction == "parents" else ["P749", "P127"]
        unions = []
        for prop in reverse_props:
            unions.append(f"""{{
                ?item p:{prop} ?statement .
                ?statement ps:{prop} wd:{identifier}; wikibase:rank ?rank .
                FILTER(?rank != wikibase:DeprecatedRank)
                FILTER NOT EXISTS {{ ?statement pq:P582 ?end . FILTER(?end <= NOW()) }}
                BIND("{prop}" AS ?property)
            }}""")
        query = PREFIXES + (
            "SELECT DISTINCT ?item ?property WHERE { "
            + " UNION ".join(unions)
            + f" }} ORDER BY ?item ?property LIMIT {self.limit + 1}"
        )
        raw_count = 0
        try:
            rows = self._query(query)
            raw_count = len(rows)
            if direction == "children":
                organizations = self._organization_ids(
                    [row["item"]["value"].rsplit("/", 1)[-1] for row in rows]
                )
                rows = [
                    row for row in rows if row["item"]["value"].rsplit("/", 1)[-1] in organizations
                ]
        except SourceError as exc:
            self.warnings.append(
                f"Wikidata reverse {direction} unavailable for {identifier}; "
                f"only explicit relationships retained: {exc}"
            )
            rows = []
        for row in rows:
            qid = row["item"]["value"].rsplit("/", 1)[-1]
            kind = "ownership" if row["property"]["value"] == "P127" else "parent_organization"
            found.append(Neighbor(qid, kind, f"https://www.wikidata.org/wiki/{qid}"))
        unique = list(dict.fromkeys(found))
        if raw_count > self.limit or len(unique) > self.limit:
            self.warnings.append(
                f"Wikidata relation-limit reached for {identifier} ({direction}); "
                "reverse statements are bounded before organization filtering; "
                "coverage is partial."
            )
        return unique[: self.limit]
