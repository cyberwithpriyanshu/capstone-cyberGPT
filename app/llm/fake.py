# app/llm/fake.py
#
# PURPOSE: A pretend AI that needs no model, no internet, and no powerful computer.
#
# WHY: While building and testing, we want to check OUR code (the API, the UI) without
# waiting for a real model, and with predictable answers. The fake provider just repeats
# what you said, so you can instantly see the whole app working.
#
# NEW IN STEP 2A: it can also "stream": it hands back its answer a few words at a time
# (with a tiny pause between words), so you can see streaming work with no model at all.

from __future__ import annotations

import re
import time
from collections.abc import Iterator

from app.llm.base import ChatMessage, LLMProvider, LLMResponse, ProviderHealth


class FakeProvider(LLMProvider):
    name = "fake"

    def __init__(
        self,
        reply: str | None = None,  # if set, always answer with this exact text
        fail_with: Exception | None = None,  # if set, pretend the model crashed with this error
        healthy: bool = True,  # lets tests simulate an "unhealthy" backend
        stream_delay: float = 0.0,  # seconds to pause between streamed words (0 = no pause)
    ) -> None:
        self.model = "fake-model"
        self._reply = reply
        self._fail_with = fail_with
        self._healthy = healthy
        self._stream_delay = stream_delay
        # Remember every conversation we received, so tests can check what was sent.
        self.calls: list[list[ChatMessage]] = []

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        self.calls.append(list(messages))

        if self._fail_with is not None:
            raise self._fail_with

        if self._reply is not None:
            content = self._reply
        else:
            # Find the most recent message written by the user and echo it back.
            last_user = next((m.content for m in reversed(messages) if m.role == "user"), "")
            content = f"[fake-llm] You said: {last_user}"

        return LLMResponse(content=content, model=self.model, provider=self.name)

    def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Iterator[str]:
        # Build the full answer the normal way (this also records the call and raises the
        # pretend failure if one was requested)...
        full = self.chat(messages, temperature=temperature, max_tokens=max_tokens).content

        # ...then hand it back in small pieces. This pattern matches "a word plus the
        # spaces after it" (or a run of spaces), so joining all pieces gives back the
        # exact original text.
        for piece in re.findall(r"\S+\s*|\s+", full):
            if self._stream_delay:
                time.sleep(self._stream_delay)
            yield piece

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            ok=self._healthy,
            provider=self.name,
            model=self.model,
            detail="fake provider" if self._healthy else "fake provider marked unhealthy",
        )