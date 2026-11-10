# app/llm/factory.py
#
# PURPOSE: Looks at the settings and creates the right AI backend.
#
# WHY: The rest of the app just says "give me the provider" and never needs to know whether
# it is the fake one or Ollama or Groq. Change one line in .env and the whole app switches.
# (A "factory" is just a function whose job is to build and return the right object.)

from __future__ import annotations

from app.config import Settings
from app.llm.base import LLMProvider
from app.llm.fake import FakeProvider
from app.llm.openai_compat import OpenAICompatProvider


def build_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "fake":
        # A small delay between words so that, when you run the app with the fake AI,
        # you can actually SEE the streaming effect.
        return FakeProvider(stream_delay=0.04)

    if settings.llm_provider == "openai_compat":
        return OpenAICompatProvider(
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            api_key=settings.llm_api_key,
            timeout=settings.llm_timeout_seconds,
            default_temperature=settings.llm_temperature,
            default_max_tokens=settings.llm_max_tokens,
        )

    # Settings already blocks unknown names, so this is only a safety net.
    raise ValueError(f"Unknown LLM provider: {settings.llm_provider}")