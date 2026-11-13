# tests/test_truncation.py
#
# PURPOSE: A model has a length limit (max_tokens). When an answer hits it, the model just
# stops, even in the middle of a code block. These tests check that we NOTICE that and pass
# it on, so the page can say "this reply was cut off" instead of showing half an answer as if
# it were finished.

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.knowledge_routes import get_search_index
from app.api.routes import get_provider
from app.config import Settings, get_settings
from app.llm import ChatMessage
from app.llm.base import LLMResponse, TextStream
from app.llm.openai_compat import OpenAICompatProvider
from app.main import app
from tests.rag_helpers import ScriptedProvider, small_index

MESSAGES = [ChatMessage("user", "hi")]


def provider_replying(handler):
    return OpenAICompatProvider(
        base_url="http://llm.test/v1", model="m", api_key="k", transport=httpx.MockTransport(handler)
    )


def sse_body(*chunks):
    """Build a streamed reply: one `data:` line per chunk, then [DONE]."""
    lines = [f"data: {json.dumps(c)}\n\n" for c in chunks] + ["data: [DONE]\n\n"]
    return "".join(lines).encode()


# ---------------------------------------------------------------- the provider notices

@pytest.mark.parametrize("finish, expected", [("length", True), ("stop", False), (None, False)])
def test_plain_chat_reports_whether_the_model_was_cut_off(finish, expected):
    def handler(request):
        choice = {"message": {"content": "partial answer"}}
        if finish is not None:
            choice["finish_reason"] = finish
        return httpx.Response(200, json={"choices": [choice]})

    assert provider_replying(handler).chat(MESSAGES).truncated is expected


@pytest.mark.parametrize("finish, expected", [("length", True), ("stop", False), (None, False)])
def test_streamed_chat_reports_whether_the_model_was_cut_off(finish, expected):
    def handler(request):
        return httpx.Response(
            200,
            content=sse_body(
                {"choices": [{"delta": {"content": "Hel"}}]},
                {"choices": [{"delta": {"content": "lo"}, "finish_reason": finish}]},
            ),
        )

    stream = provider_replying(handler).chat_stream(MESSAGES)
    text = "".join(stream)

    assert text == "Hello"               # the text itself is unaffected
    assert stream.truncated is expected


def test_each_stream_has_its_own_note():
    # Two answers at once must never share one "was it cut off?" flag.
    def handler(request):
        return httpx.Response(
            200, content=sse_body({"choices": [{"delta": {"content": "x"}, "finish_reason": "length"}]})
        )

    p = provider_replying(handler)
    first, second = p.chat_stream(MESSAGES), p.chat_stream(MESSAGES)
    list(first)
    assert first.truncated is True and second.truncated is False


# ---------------------------------------------------------------- the API passes it on

class CutOff(ScriptedProvider):
    """A pretend AI whose answer always runs into the length limit."""

    def chat(self, messages, *, temperature=None, max_tokens=None):
        return LLMResponse("half an ans", "scripted-model", "scripted", truncated=True)

    def chat_stream(self, messages, *, temperature=None, max_tokens=None):
        stream = TextStream()

        def pieces():
            yield "half an "
            yield "ans"
            stream.truncated = True

        return stream.attach(pieces())


@pytest.fixture
def client():
    app.dependency_overrides[get_provider] = lambda: CutOff()
    app.dependency_overrides[get_search_index] = small_index
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, llm_provider="fake")
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


BODY = {"messages": [{"role": "user", "content": "hello"}]}


def events(text):
    return [json.loads(b.strip()[5:].strip()) for b in text.split("\n\n") if b.strip().startswith("data:")]


def test_plain_chat_reply_says_when_it_was_cut_off(client):
    assert client.post("/chat", json=BODY).json()["truncated"] is True


def test_the_stream_says_when_the_answer_was_cut_off(client):
    done = events(client.post("/chat/stream", json=BODY).text)[-1]
    assert done["type"] == "done" and done["truncated"] is True


def test_a_complete_answer_is_not_marked(client):
    app.dependency_overrides[get_provider] = lambda: ScriptedProvider()
    done = events(client.post("/chat/stream", json=BODY).text)[-1]
    assert done["type"] == "done" and "truncated" not in done
    assert client.post("/chat", json=BODY).json()["truncated"] is False