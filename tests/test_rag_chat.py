# tests/test_rag_chat.py
#
# PURPOSE: Check the whole Step 4 path through the real API: question in -> knowledge-base
# lookup -> the AI is sent the passages -> reply comes back WITH its sources.
# We use a made-up knowledge base and a pretend AI, so we can inspect exactly what the AI
# was sent and nothing depends on the internet.

import json

import pytest
from fastapi.testclient import TestClient

from app.api.knowledge_routes import get_search_index
from app.api.routes import get_provider
from app.config import Settings, get_settings
from app.main import app
from tests.rag_helpers import ScriptedProvider, small_index


@pytest.fixture
def setup():
    """Returns a function: setup(provider, **settings) -> TestClient, with everything wired."""
    def build(provider, **settings):
        app.dependency_overrides[get_provider] = lambda: provider
        app.dependency_overrides[get_search_index] = small_index
        app.dependency_overrides[get_settings] = lambda: Settings(
            _env_file=None, llm_provider="fake", **settings
        )
        return TestClient(app)

    yield build
    app.dependency_overrides.clear()


def ask(client, text, mode="learn", path="/chat"):
    return client.post(path, json={"messages": [{"role": "user", "content": text}], "mode": mode})


def sse(text):
    return [json.loads(b.strip()[5:].strip()) for b in text.split("\n\n") if b.strip().startswith("data:")]


# ---------------------------------------------------------------- /chat

def test_reply_carries_the_sources_that_were_used(setup):
    provider = ScriptedProvider()
    client = setup(provider)

    body = ask(client, "How do I prevent SQL injection?").json()

    k = body["knowledge"]
    assert k["enabled"] is True
    assert k["sources"][0]["n"] == 1 and k["sources"][0]["id"] == "o1"
    assert k["sources"][0]["label"] == "OWASP Cheat Sheets"
    assert k["sources"][0]["url"] == "https://example.test/o1"
    assert k["searched"].startswith("How do I prevent SQL injection")


def test_the_ai_is_sent_the_passages_after_its_hidden_rules(setup):
    provider = ScriptedProvider()
    client = setup(provider)

    ask(client, "How do I prevent SQL injection?", mode="defend")

    system = provider.answer_requests[0][0]
    assert system.role == "system"
    assert "parameterized queries" in system.content          # the passage
    assert "[1] OWASP Cheat Sheets" in system.content         # its citation number
    assert "KNOWLEDGE BASE EXCERPTS" in system.content        # the rules for using it
    # ...and our own mode rules are still there, first.
    from app.modes import build_system_prompt
    assert system.content.startswith(build_system_prompt("defend"))


def test_users_own_messages_are_untouched(setup):
    provider = ScriptedProvider()
    client = setup(provider)
    ask(client, "How do I prevent SQL injection?")
    roles = [m.role for m in provider.answer_requests[0]]
    assert roles == ["system", "user"]
    assert provider.answer_requests[0][1].content == "How do I prevent SQL injection?"


def test_off_topic_question_gets_the_no_sources_instruction(setup):
    provider = ScriptedProvider()
    client = setup(provider)

    body = ask(client, "what is the weather in paris").json()

    assert body["knowledge"]["sources"] == []
    assert "nothing relevant" in provider.answer_requests[0][0].content


def test_unknown_cve_is_reported_and_guarded(setup):
    provider = ScriptedProvider()
    client = setup(provider)

    body = ask(client, "What is CVE-1999-0001?").json()

    assert body["knowledge"]["ids_not_found"] == ["CVE-1999-0001"]
    system = provider.answer_requests[0][0].content
    assert "CVE-1999-0001" in system and "NO entry" in system


def test_known_cve_is_found_exactly(setup):
    body = ask(setup(ScriptedProvider()), "What is CVE-2021-44228?").json()
    src = body["knowledge"]["sources"][0]
    assert src["ref_id"] == "CVE-2021-44228" and src["exact_id"] is True and src["tier"] == "authoritative"


def test_rewrite_runs_when_enabled_and_finds_the_renamed_topic(setup):
    provider = ScriptedProvider(keywords="Apache Log4j2 JNDI remote code execution")
    client = setup(provider, rag_rewrite=True)

    body = ask(client, "what is log4shell").json()

    assert body["knowledge"]["keywords"] == "Apache Log4j2 JNDI remote code execution"
    assert body["knowledge"]["sources"][0]["id"] == "k1"
    assert len(provider.requests) == 2  # one rewrite call + the real answer


def test_rag_can_be_switched_off(setup):
    provider = ScriptedProvider()
    client = setup(provider, rag_enabled=False)

    body = ask(client, "How do I prevent SQL injection?").json()

    assert body["knowledge"]["enabled"] is False and body["knowledge"]["sources"] == []
    assert "KNOWLEDGE BASE" not in provider.answer_requests[0][0].content


def test_a_broken_knowledge_base_does_not_break_chat(setup):
    class Broken:
        chunks = [1]
        def __len__(self): return 1
        def search(self, *a, **k): raise RuntimeError("corrupt")

    provider = ScriptedProvider()
    client = setup(provider)
    app.dependency_overrides[get_search_index] = lambda: Broken()

    r = ask(client, "How do I prevent SQL injection?")
    assert r.status_code == 200 and r.json()["knowledge"]["sources"] == []


def test_model_failure_is_still_a_502(setup):
    class Down(ScriptedProvider):
        def chat(self, messages, **kw):
            from app.llm import LLMError
            raise LLMError("model down")

    assert ask(setup(Down()), "How do I prevent SQL injection?").status_code == 502


# ---------------------------------------------------------------- /chat/stream

def test_stream_sends_sources_first_then_tokens_then_done(setup):
    client = setup(ScriptedProvider())

    events = sse(ask(client, "How do I prevent SQL injection?", path="/chat/stream").text)

    assert events[0]["type"] == "sources"
    assert events[0]["sources"][0]["id"] == "o1"
    assert [e["type"] for e in events[1:-1]] == ["token"] * (len(events) - 2)
    assert events[-1]["type"] == "done"


def test_stream_also_passes_the_passages_to_the_ai(setup):
    provider = ScriptedProvider()
    ask(setup(provider), "How do I prevent SQL injection?", path="/chat/stream")
    assert "parameterized queries" in provider.answer_requests[0][0].content


def test_stream_with_no_sources_still_announces_that(setup):
    events = sse(ask(setup(ScriptedProvider()), "what is the weather in paris", path="/chat/stream").text)
    assert events[0]["type"] == "sources" and events[0]["sources"] == [] and events[0]["enabled"] is True