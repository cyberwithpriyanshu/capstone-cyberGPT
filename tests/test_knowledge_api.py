# tests/test_knowledge_api.py
#
# PURPOSE: Test /knowledge/stats and /knowledge/search as a real caller would, using a tiny
# made-up knowledge base (swapped in with dependency_overrides) so results are predictable.

import pytest
from fastapi.testclient import TestClient

from app.api.knowledge_routes import get_search_index
from app.config import Settings
from app.knowledge import Chunk, SearchIndex
from app.main import create_app


def make(id, title, text, source="owasp", domains=("web-app",), ref_id="", tier=2):
    return Chunk(id=id, source=source, tier=tier, title=title, text=text, url=f"https://x/{id}",
                 license="test-license", domains=domains, as_of="2026-01-01", ref_id=ref_id)


CHUNKS = [
    make("o1", "SQL Injection Prevention", "Use parameterized queries. " + "More detail here. " * 60),
    make("o2", "Kubernetes Security", "Keep Kubernetes secrets safe.", domains=("defense", "cloud")),
    make("k1", "CVE-2021-44228 - Log4j", "Remote code execution via JNDI.", source="kev",
         domains=("vuln-intel",), ref_id="CVE-2021-44228", tier=1),
]


def client_with(chunks):
    app = create_app(Settings(llm_provider="fake"))
    app.dependency_overrides[get_search_index] = lambda: SearchIndex(chunks)
    return TestClient(app)


@pytest.fixture
def client():
    with client_with(CHUNKS) as c:
        yield c


def test_stats_counts_sources_and_domains(client):
    data = client.get("/knowledge/stats").json()

    assert data["ready"] is True and data["total"] == 3
    assert data["sources"] == {"owasp": 2, "kev": 1}
    assert data["domains"]["cloud"] == 1


def test_stats_explains_what_to_do_when_nothing_is_loaded():
    with client_with([]) as c:
        data = c.get("/knowledge/stats").json()

    assert data["ready"] is False and data["total"] == 0
    assert "ingest_knowledge.py" in data["hint"]


def test_search_returns_citation_fields(client):
    data = client.get("/knowledge/search", params={"q": "sql injection"}).json()

    first = data["results"][0]
    assert first["id"] == "o1"
    assert first["url"] == "https://x/o1"
    assert first["license"] == "test-license"
    assert first["source"] == "owasp" and first["tier"] == 2
    assert first["as_of"] == "2026-01-01"
    assert data["count"] == len(data["results"])


def test_long_passages_are_shortened_unless_full_is_requested(client):
    short = client.get("/knowledge/search", params={"q": "sql injection"}).json()["results"][0]
    full = client.get("/knowledge/search", params={"q": "sql injection", "full": True}).json()["results"][0]

    assert short["snippet"].endswith("...") and len(short["snippet"]) < short["text_length"]
    assert len(full["snippet"]) == full["text_length"]


def test_filters_work_through_the_api(client):
    by_domain = client.get("/knowledge/search", params={"q": "secrets", "domain": "cloud"}).json()
    by_source = client.get("/knowledge/search", params={"q": "execution", "source": "kev"}).json()

    assert [r["id"] for r in by_domain["results"]] == ["o2"]
    assert [r["id"] for r in by_source["results"]] == ["k1"]


def test_missing_ids_are_reported(client):
    data = client.get("/knowledge/search", params={"q": "CVE-2021-44228 and CVE-2099-0001"}).json()

    assert data["ids_in_query"] == ["CVE-2021-44228", "CVE-2099-0001"]
    assert data["ids_not_found"] == ["CVE-2099-0001"]
    assert data["results"][0]["exact_id"] is True


def test_unknown_domain_is_rejected_with_the_valid_choices(client):
    r = client.get("/knowledge/search", params={"q": "anything", "domain": "pizza"})

    assert r.status_code == 422
    assert "cloud" in r.json()["detail"]


@pytest.mark.parametrize("params", [{"q": "x"}, {"q": "a" * 301}, {"q": "ok query", "limit": 0}, {"q": "ok query", "limit": 21}, {}])
def test_bad_parameters_are_rejected(client, params):
    assert client.get("/knowledge/search", params=params).status_code == 422


def test_knowledge_is_behind_the_password_gate():
    app = create_app(Settings(llm_provider="fake", access_password="pw"))
    app.dependency_overrides[get_search_index] = lambda: SearchIndex(CHUNKS)
    with TestClient(app) as c:
        assert c.get("/knowledge/stats").status_code == 401
        assert c.get("/knowledge/search", params={"q": "sql"}).status_code == 401