# tests/test_streaming.py
#
# PURPOSE: Test the streaming code in the providers, again with a FAKE network so no real
# model is needed. We build a pretend server reply in the exact "Server-Sent Events"
# format that real servers (Ollama, Groq, ...) use, and check we decode it correctly.

import json

import httpx
import pytest

from app.llm import ChatMessage, LLMError
from app.llm.base import LLMProvider, LLMResponse, ProviderHealth
from app.llm.fake import FakeProvider
from app.llm.openai_compat import OpenAICompatProvider

MESSAGES = [ChatMessage("user", "hi")]


def make_provider(handler):
    return OpenAICompatProvider(
        base_url="http://llm.test/v1",
        model="llama3.2:3b",
        transport=httpx.MockTransport(handler),
    )


def chunk(text):
    """One streamed piece, shaped like a real server's data line."""
    return {"choices": [{"delta": {"content": text}}]}


def sse_body(*events, done=True):
    """Build a whole streamed reply: one `data: ...` line per event, then [DONE]."""
    body = "".join("data: " + json.dumps(e) + "\n\n" for e in events)
    if done:
        body += "data: [DONE]\n\n"
    return body


# ---------- OpenAI-compatible provider ----------


def test_stream_yields_text_pieces_in_order():
    body = sse_body(
        {"choices": [{"delta": {"role": "assistant"}}]},  # first line often has no text
        chunk("Hel"),
        chunk("lo"),
        chunk(" world"),
    )
    provider = make_provider(lambda r: httpx.Response(200, text=body))

    pieces = list(provider.chat_stream(MESSAGES))

    assert pieces == ["Hel", "lo", " world"]


def test_stream_request_asks_for_streaming():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        seen["url"] = str(request.url)
        return httpx.Response(200, text=sse_body(chunk("ok")))

    list(make_provider(handler).chat_stream(MESSAGES))

    assert seen["url"] == "http://llm.test/v1/chat/completions"
    assert seen["body"]["stream"] is True


def test_stream_ignores_keepalives_and_garbage_lines():
    body = ": keep-alive\n\n" + "data: this is not json\n\n" + sse_body(chunk("ok"))
    provider = make_provider(lambda r: httpx.Response(200, text=body))

    assert list(provider.chat_stream(MESSAGES)) == ["ok"]


def test_stream_ignores_chunks_without_choices():
    # Some servers send an empty "choices" list first; we must not crash on it.
    body = sse_body({"choices": []}, chunk("fine"))
    provider = make_provider(lambda r: httpx.Response(200, text=body))

    assert list(provider.chat_stream(MESSAGES)) == ["fine"]


def test_stream_stops_at_done_marker():
    body = sse_body(chunk("before"), done=True) + "data: " + json.dumps(chunk("after")) + "\n\n"
    provider = make_provider(lambda r: httpx.Response(200, text=body))

    assert list(provider.chat_stream(MESSAGES)) == ["before"]


def test_stream_http_error_raises_llm_error_with_server_message():
    provider = make_provider(lambda r: httpx.Response(401, text="invalid api key"))

    with pytest.raises(LLMError, match="HTTP 401"):
        list(provider.chat_stream(MESSAGES))


def test_stream_connection_error_raises_llm_error():
    def handler(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(LLMError, match="Could not reach"):
        list(make_provider(handler).chat_stream(MESSAGES))


def test_stream_timeout_raises_llm_error():
    def handler(request):
        raise httpx.ReadTimeout("slow")

    with pytest.raises(LLMError, match="Timed out"):
        list(make_provider(handler).chat_stream(MESSAGES))


# ---------- The default fallback in the base class ----------


class OneShotProvider(LLMProvider):
    """A tiny backend that does NOT implement streaming, to test the fallback."""

    name = "oneshot"
    model = "m"

    def chat(self, messages, *, temperature=None, max_tokens=None):
        return LLMResponse("full answer", "m", "oneshot")

    def health(self):
        return ProviderHealth(True, "oneshot", "m")


def test_backend_without_streaming_still_works_via_fallback():
    assert list(OneShotProvider().chat_stream(MESSAGES)) == ["full answer"]


# ---------- Fake provider streaming ----------


def test_fake_stream_joins_back_to_the_exact_reply_in_several_pieces():
    fake = FakeProvider(reply="Hello   big world\nnew line")

    pieces = list(fake.chat_stream(MESSAGES))

    assert len(pieces) > 1
    assert "".join(pieces) == "Hello   big world\nnew line"


def test_fake_stream_records_the_call():
    fake = FakeProvider()

    list(fake.chat_stream(MESSAGES))

    assert fake.calls == [MESSAGES]


def test_fake_stream_raises_the_requested_failure():
    fake = FakeProvider(fail_with=LLMError("boom"))

    with pytest.raises(LLMError, match="boom"):
        list(fake.chat_stream(MESSAGES))