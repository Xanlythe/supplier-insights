import re
from typing import Any

import httpx

from supplier_insights.http import CachedHttp, SourceError
from supplier_insights.models import Neighbor, Node

API = "https://api.gleif.org/api/v1/lei-records"


def validate_lei(lei: str) -> str:
    if not re.fullmatch(r"[A-Z0-9]{20}", lei):
        raise ValueError(f"Invalid LEI (expected 20 uppercase letters/digits): {lei}")
    return lei


class Gleif:
    name = "gleif"

    def __init__(self, http: CachedHttp, relation_limit: int = 100):
        self.http, self.limit = http, relation_limit
        self.records: dict[str, dict[str, Any]] = {}
        self.warnings: list[str] = []

    def _json(self, url: str, params=None):
        parsed = httpx.URL(url)
        if parsed.scheme != "https" or parsed.host != "api.gleif.org":
            raise SourceError(f"Untrusted GLEIF URL: {url}")
        return self.http.get_json(url, params)

    def _pages(self, url: str, limit: int, params=None) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        visited = set()
        while url:
            if url in visited:
                raise SourceError(f"GLEIF pagination cycle: {url}")
            visited.add(url)
            data = self._json(url, params)
            page = data.get("data")
            if not isinstance(page, list):
                raise SourceError(f"Expected GLEIF record list from {url}")
            records.extend(page)
            url = data.get("links", {}).get("next")
            params = None
            if len(records) >= limit:
                if url or len(records) > limit:
                    self.warnings.append(
                        f"GLEIF result limit ({limit}) reached; coverage is partial."
                    )
                break
        for record in records[:limit]:
            self.records[record["id"]] = record
        return records[:limit]

    def search(self, name: str) -> list[dict[str, str]]:
        return [
            {
                "id": record["id"],
                "name": record["attributes"]["entity"]["legalName"]["name"],
                "description": record["attributes"]["entity"].get("jurisdiction", ""),
            }
            for record in self._pages(
                API,
                10,
                {
                    "filter[entity.legalName]": name,
                    "page[size]": 10,
                },
            )
        ]

    def _record(self, identifier: str) -> dict[str, Any]:
        validate_lei(identifier)
        if identifier not in self.records:
            data = self._json(f"{API}/{identifier}").get("data")
            if not isinstance(data, dict) or data.get("id") != identifier:
                raise SourceError(f"GLEIF entity missing: {identifier}")
            self.records[identifier] = data
        return self.records[identifier]

    def get(self, identifier: str) -> Node:
        record = self._record(identifier)
        entity = record["attributes"]["entity"]
        source = f"{API}/{identifier}"
        node = Node(
            f"gleif:{identifier}",
            entity["legalName"]["name"],
            "Company",
            {"lei": [identifier]},
            sources=[source],
        )
        node.aliases = [item["name"] for item in entity.get("otherNames", []) if item.get("name")]
        for field, kind in (
            ("legalAddress", "legal_address"),
            ("headquartersAddress", "headquarters"),
        ):
            address = entity.get(field)
            if address:
                value = {
                    p: address[p]
                    for p in ("city", "country", "region", "postalCode")
                    if address.get(p)
                }
                value["full"] = ", ".join(
                    filter(
                        None,
                        [
                            *address.get("addressLines", []),
                            address.get("city"),
                            address.get("postalCode"),
                            address.get("country"),
                        ],
                    )
                )
                node.facts.append({"kind": kind, "value": value, "source": source})
        if entity.get("jurisdiction"):
            node.facts.append(
                {
                    "kind": "registration_country",
                    "value": {"country": entity["jurisdiction"]},
                    "source": source,
                }
            )
        return node

    def neighbors(self, identifier: str, direction: str) -> list[Neighbor]:
        record = self._record(identifier)
        relations = record.get("relationships", {})
        result = []
        if direction == "parents":
            for relation, kind in (
                ("direct-parent", "accounting_direct_parent"),
                ("ultimate-parent", "accounting_ultimate_parent"),
            ):
                links = relations.get(relation, {}).get("links", {})
                if links.get("lei-record"):
                    url = links["lei-record"]
                    parent = self._json(url).get("data")
                    if not isinstance(parent, dict) or "id" not in parent:
                        raise SourceError(f"Invalid GLEIF {relation} record for {identifier}")
                    self.records[parent["id"]] = parent
                    result.append(Neighbor(parent["id"], kind, url))
                elif links.get("reporting-exception"):
                    url = links["reporting-exception"]
                    exception = self._json(url)
                    self.warnings.append(
                        f"GLEIF {identifier} {relation} reporting exception: "
                        f"{exception.get('data')}; source: {url}"
                    )
                else:
                    self.warnings.append(f"GLEIF {identifier}: {relation} not reported.")
        else:
            url = relations.get("direct-children", {}).get("links", {}).get("related")
            if url:
                result.extend(
                    Neighbor(child["id"], "accounting_direct_parent", url)
                    for child in self._pages(url, self.limit, {"page[size]": min(self.limit, 200)})
                )
        return result
