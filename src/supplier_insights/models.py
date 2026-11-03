from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any

from followthemoney import model


@dataclass
class Node:
    id: str
    name: str
    schema: str
    identifiers: dict[str, list[str]]
    facts: list[dict[str, Any]] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    flags: list[dict[str, Any]] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)

    def ftm(self) -> list[dict[str, Any]]:
        entity = model.make_entity(self.schema)
        entity.id = self.id
        entity.add("name", self.name)
        for alias in self.aliases:
            entity.add("alias", alias)
        for key, prop in (("qid", "wikidataId"), ("lei", "leiCode")):
            for value in self.identifiers.get(key, []):
                entity.add(prop, value)
        for source in self.sources:
            entity.add("sourceUrl", source)
        addresses = []
        for fact in self.facts:
            if fact["kind"] not in ("headquarters", "legal_address"):
                continue
            address = model.make_entity("Address")
            address.id = "address:" + sha256(f"{self.id}:{fact}".encode()).hexdigest()[:24]
            for prop, value in fact["value"].items():
                if prop in ("full", "city", "country", "region", "postalCode"):
                    address.add(prop, value)
            address.add("sourceUrl", fact["source"])
            entity.add("addressEntity", address.id)
            addresses.append(address.to_dict())
        return [entity.to_dict(), *addresses]


@dataclass(frozen=True)
class Neighbor:
    identifier: str
    kind: str
    source: str


@dataclass(frozen=True)
class Edge:
    parent: str
    child: str
    kind: str
    source: str

    def ftm(self) -> dict[str, Any] | None:
        if self.kind != "ownership":
            return None
        entity = model.make_entity("Ownership")
        entity.id = "ownership:" + sha256(str(self).encode()).hexdigest()[:24]
        entity.add("owner", self.parent)
        entity.add("asset", self.child)
        entity.add("sourceUrl", self.source)
        return entity.to_dict()
