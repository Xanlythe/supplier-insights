import httpx
import pytest

from supplier_insights.http import CachedHttp


@pytest.fixture
def make_http(tmp_path):
    clients = []

    def make(handler, **kwargs):
        client = CachedHttp(
            tmp_path / "cache.sqlite",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            interval=0,
            **kwargs,
        )
        clients.append(client)
        return client

    yield make
    for client in clients:
        client.close()


def statement(value, **extra):
    return {"mainsnak": {"datavalue": {"value": value}}, "rank": "normal", **extra}


def wd_record(qid="Q1", name="Example", claims=None):
    return {
        "id": qid,
        "labels": {"en": {"value": name}},
        "claims": claims or {},
    }


def lei_record(lei="W38RGI023J3WT1HWRP32", name="Example", relationships=None):
    return {
        "id": lei,
        "attributes": {
            "lei": lei,
            "entity": {
                "legalName": {"name": name},
                "legalAddress": {"country": "DE", "city": "Berlin"},
                "headquartersAddress": {
                    "country": "DE",
                    "city": "Munich",
                    "addressLines": ["Example Street 1"],
                },
            },
        },
        "relationships": relationships or {},
    }
