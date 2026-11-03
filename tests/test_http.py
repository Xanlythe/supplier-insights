import httpx
import pytest

from supplier_insights.http import SourceError


def test_cache_sends_user_agent_and_does_not_repeat_requests(make_http):
    calls = []

    def handle(request):
        calls.append(request)
        assert "SupplierInsights" in request.headers["User-Agent"]
        return httpx.Response(200, json={"result": 1})

    http = make_http(handle)
    assert http.get_json("https://example.test/a", {"q": "one"}) == {"result": 1}
    assert http.get_json("https://example.test/a", {"q": "one"}) == {"result": 1}
    assert len(calls) == 1
    assert http.requests[-1]["cached"] is True
    assert http.requests[-1]["fetched_at"] == http.requests[0]["fetched_at"]


def test_retry_after_is_respected(make_http):
    sleeps = []
    responses = iter(
        [
            httpx.Response(429, headers={"Retry-After": "2"}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    http = make_http(lambda _: next(responses), sleep=sleeps.append)
    assert http.get_json("https://example.test") == {"ok": True}
    assert 2 in sleeps


def test_failed_response_is_not_cached(make_http):
    http = make_http(lambda _: httpx.Response(503), sleep=lambda _: None)
    with pytest.raises(SourceError, match="503"):
        http.get_json("https://example.test")
    assert http.requests == []


def test_expired_cache_is_refetched(make_http):
    count = 0

    def handle(_):
        nonlocal count
        count += 1
        return httpx.Response(200, json={"count": count})

    http = make_http(handle, ttl=0)
    assert http.get_json("https://example.test")["count"] == 1
    assert http.get_json("https://example.test")["count"] == 2


def test_invalid_json_and_api_errors_are_explicit(make_http):
    http = make_http(lambda _: httpx.Response(200, text="not JSON"))
    with pytest.raises(SourceError, match="JSON"):
        http.get_json("https://example.test")


def test_refresh_bypasses_cache(make_http):
    responses = iter(
        [
            httpx.Response(200, json={"version": 1}),
            httpx.Response(200, json={"version": 2}),
        ]
    )
    http = make_http(lambda _: next(responses))
    assert http.get_json("https://example.test")["version"] == 1
    assert http.get_json("https://example.test", refresh=True)["version"] == 2
