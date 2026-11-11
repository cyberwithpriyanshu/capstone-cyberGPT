# tests/test_api.py
#
# PURPOSE: Test our web addresses (/health, /modes, /chat and /chat/stream) as if a real
# user were calling them, but using the FAKE AI so the tests are fast and need no model.
#
# CHANGED IN STEP 2A (compared with Step 1B):
#  - the system-prompt test now expects the mode's instructions to be included
#  - new tests for /modes, the `mode` field, and /chat/stream

import json

import pytest

# TestClient lets us call our FastAPI app from Python, with no server and no browser.
from fastapi.testclient import TestClient

from app.api.routes import get_provider
from app.llm import LLMError
from app.llm.fake import FakeProvider
from app.main import app
from app.modes import MODES, build_system_prompt
from app.prompts import SYSTEM_PROMPT


# A "fixture" is a reusable piece of setup. Any test that lists `client` as a parameter
# receives the value we `yield` below. Code after `yield` runs when the test finishes.
@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
    # Cleanup: remove any provider swap we did, so one test can't leak into the next.
    app.dependency_overrides.clear()


# Swap the real AI backend for a fake one, just for the current test.
# `dependency_overrides` is FastAPI's built-in way to do this (see get_provider in routes.py).
def use_provider(provider):
    app.dependency_overrides[get_provider] = lambda: provider
    return provider


def parse_sse(text):
    """Turn a streamed reply (many `data: {...}` blocks) into a list of Python dictionaries."""
    events = []
    for block in text.split("\n\n"):
        block = block.strip()
        if block.startswith("data:"):
            events.append(json.loads(block[len("data:"):].strip()))
    return events


USER_HI = {"messages": [{"role": "user", "content": "hi"}]}


# ---------- basic pages ----------


def test_root_points_to_docs(client):
    r = client.get("/")

    assert r.status_code == 200
    assert r.json()["docs"] == "/docs"
    r = client.get("/")

    assert r.status_code == 200
    assert r.json()["docs"] == "/docs"


def test_health_ok(client):
    use_provider(FakeProvider())

    r = client.get("/health")

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["app"] == "CyberGPT"
    assert body["llm"]["ok"] is True


def test_health_degraded_when_llm_unhealthy_but_still_200(client):
    # healthy=False pretends the AI model is unreachable.
    use_provider(FakeProvider(healthy=False))

    r = client.get("/health")

    # Our app is fine, so the HTTP status is still 200; only the "status" field warns us.
    assert r.status_code == 200
    assert r.json()["status"] == "degraded"
    assert r.json()["llm"]["ok"] is False


# ---------- modes ----------


def test_modes_endpoint_lists_every_mode_for_the_ui(client):
    r = client.get("/modes")

    assert r.status_code == 200
    modes = r.json()
    assert [m["id"] for m in modes] == list(MODES)
    for m in modes:
        assert m["label"] and m["tagline"] and m["placeholder"]
        assert len(m["suggestions"]) >= 3


def test_modes_endpoint_never_leaks_the_hidden_instructions(client):
    r = client.get("/modes")

    for m in r.json():
        assert "instructions" not in m
    # Also check the raw text, in case the instructions sneak in under another name.
    for mode in MODES.values():
        assert mode.instructions not in r.text


# ---------- /chat ----------


def test_chat_returns_reply(client):
    use_provider(FakeProvider(reply="SQL injection is..."))

    r = client.post("/chat", json={"messages": [{"role": "user", "content": "What is SQLi?"}]})

    assert r.status_code == 200
    assert r.json() == {
        "reply": "SQL injection is...",
        "model": "fake-model",
        "provider": "fake",
    }


def test_chat_prepends_system_prompt_and_keeps_history_order(client):
    fake = use_provider(FakeProvider())

    client.post(
        "/chat",
        json={
            "messages": [
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "answer"},
                {"role": "user", "content": "second"},
            ]
        },
    )

    # The fake remembered what it received. Check that OUR hidden rules were added first
    # (using the default mode, "learn"), and the user's conversation follows in order.
    sent = fake.calls[0]
    assert [m.role for m in sent] == ["system", "user", "assistant", "user"]
    assert sent[0].content == build_system_prompt("learn")
    assert sent[0].content.startswith(SYSTEM_PROMPT)
    assert [m.content for m in sent[1:]] == ["first", "answer", "second"]


@pytest.mark.parametrize("mode_id", list(MODES))
def test_chat_uses_the_selected_modes_instructions(client, mode_id):
    fake = use_provider(FakeProvider())

    r = client.post("/chat", json={**USER_HI, "mode": mode_id})

    assert r.status_code == 200
    assert fake.calls[0][0].content == build_system_prompt(mode_id)


def test_chat_provider_failure_returns_502(client):
    use_provider(FakeProvider(fail_with=LLMError("model server down")))

    r = client.post("/chat", json=USER_HI)

    # 502 = "the server behind us (the AI model) failed", and the message explains why.
    assert r.status_code == 502
    assert "model server down" in r.json()["detail"]


# Each item below is an INVALID request. For every one, we expect HTTP 422 ("unprocessable":
# the data is the wrong shape). This proves our validation rules in schemas.py really work.
INVALID_BODIES = [
    {},  # no messages at all
    {"messages": []},  # empty list
    {"messages": [{"role": "user", "content": ""}]},  # empty text
    {"messages": [{"role": "system", "content": "sneaky"}]},  # users may not send "system"
    {"messages": [{"role": "assistant", "content": "last must be user"}]},  # must end with user
    {"messages": [{"role": "user", "content": "hi"}], "temperature": 5},  # temperature too high
    {"messages": [{"role": "user", "content": "x" * 8001}]},  # message too long
    {"messages": [{"role": "user", "content": "hi"}], "mode": "hacker"},  # unknown mode
]


@pytest.mark.parametrize("body", INVALID_BODIES)
def test_chat_rejects_invalid_requests(client, body):
    use_provider(FakeProvider())

    r = client.post("/chat", json=body)

    assert r.status_code == 422


# ---------- /chat/stream ----------


def test_stream_sends_tokens_then_done(client):
    use_provider(FakeProvider(reply="Hello there friend"))

    r = client.post("/chat/stream", json=USER_HI)

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")

    events = parse_sse(r.text)
    tokens = [e["text"] for e in events if e["type"] == "token"]
    assert len(tokens) > 1  # it arrived in several pieces, not one block
    assert "".join(tokens) == "Hello there friend"
    assert events[-1] == {"type": "done", "model": "fake-model", "provider": "fake"}


def test_stream_reports_model_failure_as_an_error_event(client):
    use_provider(FakeProvider(fail_with=LLMError("model server down")))

    r = client.post("/chat/stream", json=USER_HI)

    # Streaming already started, so the HTTP status is 200 and the error travels
    # INSIDE the stream. The UI watches for events of type "error".
    assert r.status_code == 200
    events = parse_sse(r.text)
    assert events == [{"type": "error", "message": "model server down"}]


def test_stream_uses_the_selected_mode_and_keeps_history_order(client):
    fake = use_provider(FakeProvider())

    client.post(
        "/chat/stream",
        json={
            "messages": [
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "answer"},
                {"role": "user", "content": "second"},
            ],
            "mode": "defend",
        },
    )

    sent = fake.calls[0]
    assert sent[0].content == build_system_prompt("defend")
    assert [m.content for m in sent[1:]] == ["first", "answer", "second"]


@pytest.mark.parametrize("body", INVALID_BODIES)
def test_stream_rejects_invalid_requests_before_streaming(client, body):
    use_provider(FakeProvider())

    r = client.post("/chat/stream", json=body)

    assert r.status_code == 422