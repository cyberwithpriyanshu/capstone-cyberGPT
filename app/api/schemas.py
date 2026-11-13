# app/api/schemas.py
#
# PURPOSE: Describes exactly what data our web addresses accept and return.
#
# WHY: Anyone (or any buggy UI code) can send us anything. These "schemas" make FastAPI
# check every incoming request automatically. Bad requests are rejected with a clear error
# (HTTP 422) BEFORE reaching our logic, so we never have to write that checking by hand.
# FastAPI also uses them to build the interactive docs page at /docs.
#
# NEW IN STEP 2A: ChatRequest has a `mode`, and ModeInfo describes a mode for the UI.

from __future__ import annotations

from typing import Literal

# BaseModel = base class for "validated data shapes". Field = extra rules for a value.
from pydantic import BaseModel, Field, field_validator, model_validator

from app.modes import DEFAULT_MODE, MODES


class Message(BaseModel):
    # Users may only send "user" or "assistant" messages. We do NOT accept "system"
    # from outside, otherwise a user could override our safety rules in the system prompt.
    role: Literal["user", "assistant"]

    # Not empty, and not absurdly long (protects the small model and your computer).
    content: str = Field(min_length=1, max_length=8000)


class ChatRequest(BaseModel):
    # The conversation so far: between 1 and 50 messages.
    messages: list[Message] = Field(min_length=1, max_length=50)

    # Optional creativity override, allowed range 0 to 2.
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)

    # Which mode to answer in (learn, analyze, pentest, defend, report).
    # If the UI doesn't send one, we use the default.
    mode: str = DEFAULT_MODE

    # Reject unknown modes with a helpful message listing the valid ones.
    @field_validator("mode")
    @classmethod
    def mode_must_exist(cls, value: str) -> str:
        if value not in MODES:
            raise ValueError(f"Unknown mode '{value}'. Choose one of: {', '.join(MODES)}")
        return value

    # A custom rule: the conversation must END with a user message, because that is the
    # thing the AI is supposed to answer.
    @model_validator(mode="after")
    def last_message_must_be_user(self) -> "ChatRequest":
        if self.messages[-1].role != "user":
            raise ValueError("The last message must have role 'user'")
        return self


class Source(BaseModel):
    # One knowledge-base passage the AI was given. `n` is its citation number: when the
    # answer says [2], it points at the source whose n is 2.
    n: int
    id: str
    title: str
    source: str  # short code: kev, attack or owasp
    label: str  # human name: "CISA KEV", "MITRE ATT&CK", "OWASP Cheat Sheets"
    tier: str  # "authoritative" or "community"
    url: str  # the original page
    license: str
    as_of: str
    ref_id: str  # official ID like CVE-2021-44228 (may be empty)
    exact_id: bool  # True = the user named this ID and we matched it exactly
    excerpt: str  # the start of the passage, for a quick look


class SourcesInfo(BaseModel):
    """Everything about the knowledge-base lookup for one question."""

    enabled: bool = True  # False when RAG is switched off in .env
    sources: list[Source] = []
    # IDs the user mentioned that we have NO entry for. This does NOT prove they don't exist.
    ids_not_found: list[str] = []
    searched: str = ""  # the text we searched with (transparency: shown on the page)
    keywords: str = ""  # the AI's suggested keywords, if rewriting ran


class ChatResponse(BaseModel):
    reply: str  # the AI's answer
    model: str  # which model produced it
    provider: str  # which backend was used
    # NEW in Step 4. Optional with a default, so older callers keep working.
    knowledge: SourcesInfo = SourcesInfo()
    # NEW: True when the model hit its length limit and the reply is cut off mid-way.
    truncated: bool = False


class ModeInfo(BaseModel):
    # What the UI needs to draw a mode button. Notice what is NOT here: the hidden
    # `instructions`. The rules we give the AI are never sent to the browser.
    id: str
    label: str
    tagline: str
    placeholder: str
    suggestions: list[str]


class LLMHealth(BaseModel):
    ok: bool
    provider: str
    model: str
    detail: str
    available_models: list[str] = []


class HealthResponse(BaseModel):
    # "ok" = everything works. "degraded" = our app runs but the AI model isn't reachable.
    status: Literal["ok", "degraded"]
    app: str
    version: str
    llm: LLMHealth