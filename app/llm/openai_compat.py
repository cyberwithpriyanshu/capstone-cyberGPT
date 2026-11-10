# app/llm/openai_compat.py
#
# PURPOSE: The code that talks to a REAL AI model over HTTP.
#
# WHY "openai_compat"? OpenAI (the ChatGPT company) defined a standard format for sending
# chat messages to an AI server. Ollama, Groq, LM Studio, llama.cpp and many others copy
# that same format. So this ONE file works with all of them; we never rewrite it when we
# change the model or the server. Only the settings in .env change.
#
# NEW IN STEP 2A: chat_stream(), which reads the answer piece by piece as it is generated.

from __future__ import annotations

import json
from collections.abc import Iterator

import httpx  # sends HTTP requests, like a web browser would, but from our code

from app.llm.base import (
    ChatMessage,
    LLMError,
    LLMProvider,
    LLMResponse,
    ProviderHealth,
)


class OpenAICompatProvider(LLMProvider):
    name = "openai_compat"

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout: float = 120.0,
        default_temperature: float = 0.2,
        default_max_tokens: int = 1024,
        # `transport` is only used by automated tests, to fake the network.
        # In normal use it stays None and real network requests are made.
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.model = model
        # rstrip("/") removes a trailing "/" so we never end up with "//" in web addresses.
        self._base_url = base_url.rstrip("/")
        self._default_temperature = default_temperature
        self._default_max_tokens = default_max_tokens

        # Headers are extra info sent with every request.
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        # One reusable HTTP client for all requests (faster than creating one each time).
        # We add a trailing "/" to base_url so that later calls like post("chat/completions")
        # become <base_url>/chat/completions.
        self._client = httpx.Client(
            base_url=self._base_url + "/",
            headers=headers,
            timeout=timeout,
            transport=transport,
        )

    # ---------- helpers shared by chat() and chat_stream() ----------

    def _build_payload(
        self,
        messages: list[ChatMessage],
        temperature: float | None,
        max_tokens: int | None,
        stream: bool,
    ) -> dict:
        # The request body in the standard format the server expects.
        return {
            "model": self.model,
            "messages": [m.to_dict() for m in messages],
            # If the caller didn't pick a temperature, use our default from settings.
            "temperature": self._default_temperature if temperature is None else temperature,
            "max_tokens": max_tokens or self._default_max_tokens,
            # stream=False: "send the whole answer at once".
            # stream=True:  "send the answer in small pieces as you write it".
            "stream": stream,
        }

    def _timeout_error(self) -> LLMError:
        return LLMError(f"Timed out waiting for the LLM server at {self._base_url}")

    def _unreachable_error(self, exc: Exception) -> LLMError:
        # Most common cause: Ollama isn't running (or the address in .env is wrong).
        return LLMError(
            f"Could not reach the LLM server at {self._base_url} ({exc.__class__.__name__}). "
            "Is it running?"
        )

    # ---------- normal (non-streaming) chat ----------

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        payload = self._build_payload(messages, temperature, max_tokens, stream=False)

        # Try to send the request. Different things can go wrong, and we turn each one
        # into a clear LLMError message instead of letting a confusing crash happen.
        try:
            resp = self._client.post("chat/completions", json=payload)
        except httpx.TimeoutException as exc:
            raise self._timeout_error() from exc
        except httpx.HTTPError as exc:
            raise self._unreachable_error(exc) from exc

        # 200 means "OK" in HTTP. Anything else means the server complained.
        if resp.status_code != 200:
            raise LLMError(f"LLM server returned HTTP {resp.status_code}: {resp.text[:300]}")

        # Dig the answer text out of the reply. The standard format is:
        #   {"choices": [{"message": {"content": "the answer"}}], ...}
        # If the reply has a different shape, we raise a clear error instead of crashing.
        try:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError("Unexpected response format from the LLM server") from exc

        if not isinstance(content, str):
            raise LLMError("Unexpected response format from the LLM server")

        return LLMResponse(
            content=content.strip(),  # strip() removes stray spaces/newlines at the ends
            model=str(data.get("model") or self.model),
            provider=self.name,
        )

    # ---------- streaming chat ----------

    def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Iterator[str]:
        payload = self._build_payload(messages, temperature, max_tokens, stream=True)

        try:
            # client.stream(...) keeps the connection open and lets us read the reply
            # line by line while the model is still writing it.
            with self._client.stream("POST", "chat/completions", json=payload) as resp:
                if resp.status_code != 200:
                    resp.read()  # load the error message body so we can show it
                    raise LLMError(
                        f"LLM server returned HTTP {resp.status_code}: {resp.text[:300]}"
                    )

                # A streamed reply is a series of lines in "Server-Sent Events" format:
                #   data: {"choices":[{"delta":{"content":"Hel"}}]}
                #   data: {"choices":[{"delta":{"content":"lo"}}]}
                #   data: [DONE]
                # Each "data:" line carries a small piece of the answer ("delta").
                for line in resp.iter_lines():
                    if not line.startswith("data:"):
                        continue  # skip blank lines and keep-alive comments like ": ping"

                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        break  # the server says the answer is finished

                    try:
                        chunk = json.loads(data)
                        piece = chunk["choices"][0]["delta"].get("content")
                    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
                        continue  # ignore any line we don't understand instead of crashing

                    if piece:
                        yield piece
        except httpx.TimeoutException as exc:
            raise self._timeout_error() from exc
        except httpx.HTTPError as exc:
            raise self._unreachable_error(exc) from exc

    # ---------- health check ----------

    def health(self) -> ProviderHealth:
        # Ask the server "which models do you have?". If it answers, the server is alive.
        try:
            resp = self._client.get("models", timeout=5.0)
        except httpx.HTTPError as exc:
            return ProviderHealth(
                ok=False,
                provider=self.name,
                model=self.model,
                detail=f"Cannot reach {self._base_url} ({exc.__class__.__name__}). Is the LLM server running?",
            )

        if resp.status_code != 200:
            return ProviderHealth(
                ok=False,
                provider=self.name,
                model=self.model,
                detail=f"LLM server returned HTTP {resp.status_code} for /models",
            )

        # Collect the names of installed models from the reply.
        try:
            ids = tuple(str(item["id"]) for item in resp.json().get("data", []))
        except (ValueError, KeyError, TypeError, AttributeError):
            ids = ()

        # If the server listed models but ours isn't one of them, tell the user how to fix it.
        # Ollama stores "llama3.2" as "llama3.2:latest", so we accept that form too.
        # (Some servers don't list models at all; an empty list means "can't check", so we allow it.)
        if ids and self.model not in ids and f"{self.model}:latest" not in ids:
            return ProviderHealth(
                ok=False,
                provider=self.name,
                model=self.model,
                detail=f"Server is reachable but model '{self.model}' is not installed. "
                f"For Ollama run: ollama pull {self.model}",
                available_models=ids,
            )

        return ProviderHealth(
            ok=True,
            provider=self.name,
            model=self.model,
            detail="reachable",
            available_models=ids,
        )