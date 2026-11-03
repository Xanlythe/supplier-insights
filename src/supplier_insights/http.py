"""Serialized, cached HTTP with explicit failures and bounded retries."""

import json
import sqlite3
import time
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import httpx

USER_AGENT = (
    "SupplierInsights/0.1 (personal non-commercial supply-chain research; "
    "https://github.com/Xanlythe/supplier-insights)"
)


class SourceError(RuntimeError):
    """A source could not provide usable data."""


class CachedHttp:
    def __init__(
        self,
        path: Path,
        *,
        client: httpx.Client | None = None,
        user_agent: str = USER_AGENT,
        ttl: float = 86400,
        interval: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS responses "
            "(url TEXT PRIMARY KEY, fetched REAL NOT NULL, body TEXT NOT NULL)"
        )
        self.client = client or httpx.Client(timeout=60, follow_redirects=True)
        self.headers = {"User-Agent": user_agent, "Accept": "application/json"}
        self.ttl, self.interval, self.sleep = ttl, interval, sleep
        self.last_request: dict[str, float] = {}
        self.requests: list[dict[str, Any]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        self.client.close()
        self.db.close()

    def throttle(self, url: str):
        host = httpx.URL(url).host
        wait = self.interval - (time.monotonic() - self.last_request.get(host, 0))
        if wait > 0:
            self.sleep(wait)
        self.last_request[host] = time.monotonic()

    def get_json(
        self, url: str, params: dict[str, Any] | None = None, *, refresh: bool = False
    ) -> dict[str, Any]:
        request = self.client.build_request("GET", url, params=params, headers=self.headers)
        key = str(request.url)
        row = self.db.execute("SELECT fetched, body FROM responses WHERE url=?", (key,)).fetchone()
        cached = not refresh and row is not None and time.time() - row[0] < self.ttl
        if cached:
            fetched, body = row
            data = json.loads(body)
        else:
            data = self._fetch(key)
            fetched = time.time()
            with self.db:
                self.db.execute(
                    "INSERT OR REPLACE INTO responses VALUES (?, ?, ?)",
                    (key, fetched, json.dumps(data)),
                )
        self.requests.append(
            {
                "url": key,
                "fetched_at": datetime.fromtimestamp(fetched, UTC).isoformat(),
                "cached": cached,
            }
        )
        return data

    def _fetch(self, url: str) -> dict[str, Any]:
        for attempt in range(3):
            self.throttle(url)
            try:
                response = self.client.get(url, headers=self.headers)
            except httpx.TransportError as exc:
                if attempt < 2:
                    self.sleep(2**attempt)
                    continue
                raise SourceError(f"Request failed for {url}: {exc}") from exc
            if response.status_code == 429 or 500 <= response.status_code < 600:
                if attempt < 2:
                    retry = response.headers.get("Retry-After")
                    try:
                        delay = float(retry) if retry else float(2**attempt)
                    except ValueError:
                        try:
                            delay = (
                                parsedate_to_datetime(retry) - datetime.now(UTC)
                            ).total_seconds()
                        except (TypeError, ValueError, OverflowError) as exc:
                            raise SourceError(f"Invalid Retry-After from {url}: {retry}") from exc
                    if delay > 120:
                        raise SourceError(f"Rate limited by {url}; retry after {retry}")
                    self.sleep(max(0, delay))
                    continue
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise SourceError(f"HTTP {response.status_code} from {url}") from exc
            try:
                data = response.json()
            except ValueError as exc:
                raise SourceError(f"Invalid JSON from {url}") from exc
            if not isinstance(data, dict):
                raise SourceError(f"Expected a JSON object from {url}")
            if data.get("error") or data.get("errors"):
                raise SourceError(f"API error from {url}: {data.get('error') or data['errors']}")
            return data
        raise AssertionError("Retry loop must return or raise")

    def download(self, url: str, path: Path):
        self.throttle(url)
        try:
            with self.client.stream("GET", url, headers=self.headers, timeout=180) as response:
                response.raise_for_status()
                with path.open("wb") as stream:
                    for chunk in response.iter_bytes():
                        stream.write(chunk)
        except httpx.HTTPError as exc:
            raise SourceError(f"Download failed for {url}: {exc}") from exc
