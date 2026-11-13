# app/llm/base.py
#
# PURPOSE: Defines the "shapes" of data and the "contract" every AI backend must follow.
#
# WHY: Today we use a small model through Ollama. Later you might want a bigger model or a
# cloud API. If the rest of the app only depends on THIS contract, switching backends means
# adding one new file, not rewriting the whole project. This is called an "interface".
#
# NEW IN STEP 2A: chat_stream(), which hands back the answer in small pieces as the model
# writes it, so the UI can show text appearing word by word instead of waiting for the end.

# This line lets us write type hints like `float | None` on older Python versions too.
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass


class LLMError(Exception):
    """Our own error type for 'something went wrong talking to the AI model'.

    Why a custom error? Everything that can fail (server off, timeout, weird reply) is
    turned into THIS one error, so the rest of the app has one thing to catch.
    """


# A @dataclass is a quick way to make a small "record" that just holds data.
# frozen=True means that once created, it can't be changed by accident.
@dataclass(frozen=True)
class ChatMessage:
    role: str  # who is speaking: "system" (rules), "user" (the human), or "assistant" (the AI)
    content: str  # the actual text of the message

    def to_dict(self) -> dict[str, str]:
        # The AI server expects plain dictionaries like {"role": "user", "content": "hi"}.
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True)
class LLMResponse:
    content: str  # the model's answer
    model: str  # which model answered
    provider: str  # which backend was used (e.g. "openai_compat" or "fake")
    # NEW in Step 4: True when the model ran out of its allowed length (max_tokens) and the
    # answer was cut off in the middle. The page then tells the reader, instead of showing a
    # half-finished answer as if it were complete.
    truncated: bool = False


class TextStream:
    """The pieces of a streamed answer, plus a note about HOW the answer ended.

    You use it exactly like any iterator of text:   for piece in stream: ...
    After the loop, `stream.truncated` says whether the model was cut off by the length limit.
    (Each call to chat_stream() gets its OWN TextStream, so two people chatting at the same
    time can never mix up each other's result.)
    """

    def __init__(self) -> None:
        self.truncated = False
        self._source: Iterator[str] = iter(())

    def attach(self, source: Iterator[str]) -> "TextStream":
        self._source = source
        return self

    def __iter__(self) -> Iterator[str]:
        return self._source


@dataclass(frozen=True)
class ProviderHealth:
    ok: bool  # True = ready to use
    provider: str
    model: str
    detail: str = ""  # human-readable explanation (e.g. "Is Ollama running?")
    available_models: tuple[str, ...] = ()  # models the server says it has installed


# ABC = "Abstract Base Class". It is a template that cannot be used directly; it only
# says which methods every real backend MUST provide.
class LLMProvider(ABC):
    """Every backend (Ollama, llama.cpp, cloud API, fake) implements this."""

    name: str
    model: str

    @abstractmethod
    def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Send a conversation, get the model's reply. Raises LLMError on failure."""

    @abstractmethod
    def health(self) -> ProviderHealth:
        """Report whether the backend is reachable and the model is installed.
        This must NEVER crash; it should return ok=False with an explanation instead."""

    # NOT abstract: backends are NOT forced to write this. A "generator" is a function that
    # uses `yield` to hand back one piece at a time instead of everything at once.
    # This default simply waits for the full answer and yields it as one big piece, so a
    # backend that cannot stream still works (the text just appears all at once).
    # Backends that CAN stream override this method.
    def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Iterator[str]:
        """Yield the reply in pieces. Raises LLMError on failure."""
        yield self.chat(messages, temperature=temperature, max_tokens=max_tokens).content