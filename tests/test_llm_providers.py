# tests/test_llm_providers.py
#
# PURPOSE: Check the AI backends in app/llm/ WITHOUT needing a real model.
#
# THE KEY TRICK: httpx.MockTransport is a "fake network". Instead of really sending a
# request over the internet, httpx hands the request to a small function we write, and
# that function returns a made-up reply. This lets us test situations that are hard to
# create for real: the server being down, a timeout, garbage replies, and so on.

import json

import httpx
import pytest

from app.config import Settings
from app.llm import ChatMessage, LLMError, build_provider
from app.llm.fake import FakeProvider
from app.llm.openai_compat import OpenAICompatProvider


# A helper (not a test, because its name doesn't start with "test_") that builds a provider
# wired to our fake network. `handler` is the function that decides what the "server" replies.
def make_provider(handler, model="llama3.2:3b"):
    return OpenAICompatProvider(
        base_url="http://llm.test/v1",
        model=model,
        api_key="secret",
        transport=httpx.MockTransport(handler),
    )


# A sample conversation reused by many tests.
MESSAGES = [ChatMessage("system", "be brief"), ChatMessage("user", "hi")]


# ---------- OpenAI-compatible provider: chat ----------


def test_chat_success_sends_correct_request_and_parses_reply():
    # We use this dictionary to "spy" on what our code sent, so we can check it afterwards.
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        # Reply exactly like a real server would (note the messy spaces around "hello there").
        return httpx.Response(
            200,
            json={
                "model": "llama3.2:3b",
                "choices": [{"message": {"role": "assistant", "content": "  hello there \n"}}],
            },
        )

    result = make_provider(handler).chat(MESSAGES, temperature=0.5)

    # Did we call the right web address, with the right password header and body?
    assert seen["url"] == "http://llm.test/v1/chat/completions"
    assert seen["auth"] == "Bearer secret"
    assert seen["body"]["model"] == "llama3.2:3b"
    assert seen["body"]["temperature"] == 0.5
    assert seen["body"]["stream"] is False
    assert seen["body"]["messages"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hi"},
    ]
    # Was the reply cleaned up (extra spaces removed)?
    assert result.content == "hello there"
    assert result.provider == "openai_compat"


def test_chat_uses_default_temperature_when_not_given():
    seen = {}

    # `lambda` style isn't needed here; this small function just records the request body.
    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    make_provider(handler).chat(MESSAGES)

    assert seen["body"]["temperature"] == 0.2


def test_chat_http_error_raises_llm_error():
    # `lambda r: ...` is a tiny one-line function: "given a request r, return this response".
    provider = make_provider(lambda r: httpx.Response(500, text="boom"))

    # `match=` checks that the error message contains this text.
    with pytest.raises(LLMError, match="HTTP 500"):
        provider.chat(MESSAGES)


# @pytest.mark.parametrize runs the SAME test several times, once per item in the list.
# Here each item is a different kind of broken reply. pytest counts each as its own test.
@pytest.mark.parametrize(
    "payload",
    [
        {},  # nothing useful
        {"choices": []},  # no answers
        {"choices": [{"message": {}}]},  # answer has no text
        {"choices": [{"message": {"content": None}}]},  # text is null
    ],
)
def test_chat_malformed_payload_raises_llm_error(payload):
    provider = make_provider(lambda r: httpx.Response(200, json=payload))

    with pytest.raises(LLMError, match="Unexpected response"):
        provider.chat(MESSAGES)


def test_chat_non_json_body_raises_llm_error():
    # The server answers with a web page instead of data (happens with wrong addresses).
    provider = make_provider(lambda r: httpx.Response(200, text="<html>nope</html>"))

    with pytest.raises(LLMError, match="Unexpected response"):
        provider.chat(MESSAGES)


def test_chat_connection_error_raises_llm_error():
    # Simulates "Ollama is not running": the connection is refused.
    def handler(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(LLMError, match="Could not reach"):
        make_provider(handler).chat(MESSAGES)


def test_chat_timeout_raises_llm_error():
    # Simulates a model that takes too long to answer.
    def handler(request):
        raise httpx.ReadTimeout("slow")

    with pytest.raises(LLMError, match="Timed out"):
        make_provider(handler).chat(MESSAGES)


# ---------- OpenAI-compatible provider: health ----------


# Helper that builds a fake "list of installed models" reply.
def models_response(*ids):
    return httpx.Response(200, json={"data": [{"id": i} for i in ids]})


def test_health_ok_when_model_installed():
    h = make_provider(lambda r: models_response("llama3.2:3b", "qwen2.5:3b")).health()

    assert h.ok is True
    assert "llama3.2:3b" in h.available_models


def test_health_accepts_latest_tag():
    # Ollama stores "llama3.2" as "llama3.2:latest"; we should treat these as the same model.
    h = make_provider(lambda r: models_response("llama3.2:latest"), model="llama3.2").health()

    assert h.ok is True


def test_health_fails_with_pull_hint_when_model_missing():
    # The server is up but doesn't have our model: the message should tell the user the fix.
    h = make_provider(lambda r: models_response("other:1b")).health()

    assert h.ok is False
    assert "ollama pull llama3.2:3b" in h.detail


def test_health_ok_when_server_lists_no_models():
    # Some servers don't list their models. We can't check, so we allow it.
    h = make_provider(lambda r: models_response()).health()

    assert h.ok is True


def test_health_does_not_raise_when_server_down():
    def handler(request):
        raise httpx.ConnectError("refused")

    # IMPORTANT: health() must never crash, even when the server is off.
    h = make_provider(handler).health()

    assert h.ok is False
    assert "Cannot reach" in h.detail


def test_health_reports_http_error_status():
    h = make_provider(lambda r: httpx.Response(401)).health()

    assert h.ok is False
    assert "401" in h.detail


# ---------- Fake provider ----------


def test_fake_provider_echoes_last_user_message_and_records_calls():
    fake = FakeProvider()

    result = fake.chat(MESSAGES)

    assert result.content == "[fake-llm] You said: hi"
    # The fake remembers what it received, which the API tests use later.
    assert fake.calls == [MESSAGES]


def test_fake_provider_can_fail_on_demand():
    fake = FakeProvider(fail_with=LLMError("boom"))

    with pytest.raises(LLMError, match="boom"):
        fake.chat(MESSAGES)


# ---------- Factory ----------


def test_factory_builds_fake_provider():
    provider = build_provider(Settings(_env_file=None, llm_provider="fake"))

    assert isinstance(provider, FakeProvider)


def test_factory_builds_openai_compat_provider_from_settings():
    settings = Settings(
        _env_file=None,
        llm_provider="openai_compat",
        llm_base_url="http://llm.test/v1",
        llm_model="qwen2.5:3b",
    )

    provider = build_provider(settings)

    assert isinstance(provider, OpenAICompatProvider)
    assert provider.model == "qwen2.5:3b"