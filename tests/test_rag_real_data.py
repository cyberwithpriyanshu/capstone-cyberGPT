# tests/test_rag_real_data.py
#
# PURPOSE: Check retrieval against your REAL knowledge base (data/knowledge/*.jsonl), using
# realistic questions. This is the test that notices if a future change to the ranking makes
# answers worse. It is skipped automatically if you have not run the ingest script yet.

import pytest

from app.knowledge import DEFAULT_DATA_DIR, SearchIndex, load_directory
from app.rag.retrieval import retrieve

pytestmark = pytest.mark.skipif(
    not list(DEFAULT_DATA_DIR.glob("*.jsonl")),
    reason="run `python scripts/ingest_knowledge.py` first",
)


@pytest.fixture(scope="module")
def index():
    return SearchIndex(load_directory(DEFAULT_DATA_DIR))


def find(index, question):
    return retrieve(index, None, [("user", question)], rewrite=False, max_sources=4)


@pytest.mark.parametrize(
    "question, expected_in_titles",
    [
        ("How do I prevent SQL injection?", "sql injection"),
        ("What is Kerberoasting?", "kerberoasting"),
        ("How do I secure Docker containers?", "docker"),
        ("What is cross site request forgery?", "forgery"),
        ("What is CORS misconfiguration?", "cors"),
        ("what is lateral movement", "lateral"),
        ("how to detect mimikatz", "mimikatz"),
    ],
)
def test_realistic_questions_find_a_matching_passage(index, question, expected_in_titles):
    r = find(index, question)
    titles = " | ".join(h.chunk.title.lower() for h in r.hits)
    assert expected_in_titles in titles, titles


@pytest.mark.parametrize(
    "question",
    ["tell me a joke about cats", "what is the weather in paris", "who won the football world cup",
     "how to cook pasta", "hi", "write me a poem about the sea"],
)
def test_off_topic_questions_find_nothing(index, question):
    assert find(index, question).hits == []


def test_exact_cve_lookup_works_on_real_data(index):
    r = find(index, "explain CVE-2021-44228")
    assert r.hits[0].chunk.ref_id == "CVE-2021-44228" and r.hits[0].exact_id


def test_a_cve_that_is_not_in_the_sources_is_reported(index):
    r = find(index, "tell me about CVE-1999-0001")
    assert r.ids_not_found == ["CVE-1999-0001"] and r.hits == []