# app/api/routes.py
#
# PURPOSE: Defines our web addresses ("routes"):
#   GET  /health       -> "is the app alive, and can it reach the AI model?"
#   GET  /modes        -> "which modes can the user pick?" (NEW in Step 2A)
#   POST /chat         -> "here is a conversation, give me the AI's full reply"
#   POST /chat/stream  -> same, but the reply arrives word by word (NEW in Step 2A)
#
# NEW IN STEP 4: both chat routes first look the question up in the knowledge base
# (app/rag), give the AI the matching passages, and send the sources back with the reply.

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import asdict
from functools import lru_cache

# APIRouter groups routes together. Depends lets FastAPI hand ready-made objects to our
# functions. HTTPException lets us return proper HTTP errors.
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.api.knowledge_routes import get_search_index
from app.api.schemas import (
    ChatRequest,
    ChatResponse,
    HealthResponse,
    LLMHealth,
    ModeInfo,
    Source,
    SourcesInfo,
)
from app.config import Settings, get_settings
from app.knowledge import SearchIndex
from app.llm import ChatMessage, LLMError, LLMProvider, build_provider
from app.modes import MODES, build_system_prompt
from app.rag import Retrieval, build_sources_block, retrieve, source_cards

router = APIRouter()


# Create the AI backend ONCE and reuse it (lru_cache = remember the result).
# Routes receive it through `Depends(get_provider)`. Automated tests can swap in the fake
# provider here without touching the real routes; this trick is called "dependency injection".
@lru_cache
def get_provider() -> LLMProvider:
    return build_provider(get_settings())


def look_up(
    req: ChatRequest,
    provider: LLMProvider,
    index: SearchIndex,
    settings: Settings,
) -> Retrieval | None:
    """Search the knowledge base for the latest question. Returns None when RAG is off."""
    if not settings.rag_enabled:
        return None
    history = [(m.role, m.content) for m in req.messages]
    return retrieve(
        index,
        provider,
        history,
        rewrite=settings.rag_rewrite,
        max_sources=settings.rag_max_sources,
    )


def build_messages(req: ChatRequest, retrieval: Retrieval | None = None) -> list[ChatMessage]:
    """Turn the user's request into the full conversation we send to the AI.

    Both /chat and /chat/stream use this, so they always behave identically.
    """
    # Start with OUR hidden rules (base safety rules + the chosen mode's instructions),
    # then add the user's messages after it. Users can't send "system" messages
    # (see schemas.py), so they cannot replace these rules.
    system = build_system_prompt(req.mode)

    # NEW in Step 4: when we searched the knowledge base, the passages we found (and the
    # rules for citing them) are appended to the hidden instructions for THIS question.
    if retrieval is not None:
        system = f"{system}\n{build_sources_block(retrieval)}"

    messages = [ChatMessage("system", system)]
    messages += [ChatMessage(m.role, m.content) for m in req.messages]
    return messages


def knowledge_info(retrieval: Retrieval | None) -> SourcesInfo:
    """The sources part of the reply, in the shape the web page expects."""
    if retrieval is None:
        return SourcesInfo(enabled=False)
    return SourcesInfo(
        enabled=True,
        sources=[Source(**asdict(card)) for card in source_cards(retrieval.hits)],
        ids_not_found=retrieval.ids_not_found,
        searched=retrieval.searched,
        keywords=retrieval.keywords,
    )


@router.get("/health", response_model=HealthResponse)
def health(
    settings: Settings = Depends(get_settings),
    provider: LLMProvider = Depends(get_provider),
) -> HealthResponse:
    # Ask the backend about itself. This never crashes (see base.py).
    ph = provider.health()

    # We return HTTP 200 even when the model is down: OUR app is fine, and the "status"
    # field tells you the model has a problem ("degraded"). The UI will use this to show
    # a friendly "model offline" message.
    return HealthResponse(
        status="ok" if ph.ok else "degraded",
        app=settings.app_name,
        version=settings.app_version,
        llm=LLMHealth(
            ok=ph.ok,
            provider=ph.provider,
            model=ph.model,
            detail=ph.detail,
            available_models=list(ph.available_models),
        ),
    )


@router.get("/modes", response_model=list[ModeInfo])
def list_modes() -> list[ModeInfo]:
    # The UI calls this once when it loads, so the mode buttons always match modes.py.
    # We copy only the safe, display-related fields (never the hidden instructions).
    return [
        ModeInfo(
            id=m.id,
            label=m.label,
            tagline=m.tagline,
            placeholder=m.placeholder,
            suggestions=list(m.suggestions),
        )
        for m in MODES.values()
    ]


@router.post("/chat", response_model=ChatResponse)
def chat(
    req: ChatRequest,  # FastAPI checks the incoming data against ChatRequest automatically
    provider: LLMProvider = Depends(get_provider),
    index: SearchIndex = Depends(get_search_index),
    settings: Settings = Depends(get_settings),
) -> ChatResponse:
    retrieval = look_up(req, provider, index, settings)
    try:
        result = provider.chat(build_messages(req, retrieval), temperature=req.temperature)
    except LLMError as exc:
        # 502 "Bad Gateway" means: our server is fine, but the server behind it
        # (the AI model) failed. The message tells the user what to check.
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return ChatResponse(
        reply=result.content,
        model=result.model,
        provider=result.provider,
        knowledge=knowledge_info(retrieval),
    )


def sse_event(payload: dict) -> str:
    """Format one message in "Server-Sent Events" style: a line starting with `data: `
    followed by a blank line. Browsers (and our UI) understand this format."""
    return f"data: {json.dumps(payload)}\n\n"


@router.post("/chat/stream")
def chat_stream(
    req: ChatRequest,
    provider: LLMProvider = Depends(get_provider),
    index: SearchIndex = Depends(get_search_index),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    retrieval = look_up(req, provider, index, settings)
    messages = build_messages(req, retrieval)
    info = knowledge_info(retrieval)

    # A generator: each `yield` sends one small message to the browser immediately.
    # Four kinds of messages are sent:
    #   {"type": "sources", ...}           (NEW in Step 4) the knowledge-base passages the AI
    #                                      was given; always the FIRST message
    #   {"type": "token", "text": "..."}   a piece of the answer
    #   {"type": "done", ...}              the answer is complete
    #   {"type": "error", "message": "..."} something failed
    # WHY an "error" message instead of an HTTP error code? Once streaming has started the
    # HTTP status (200) has already been sent, so errors have to travel inside the stream.
    def event_stream() -> Iterator[str]:
        yield sse_event({"type": "sources", **info.model_dump()})
        try:
            for piece in provider.chat_stream(messages, temperature=req.temperature):
                yield sse_event({"type": "token", "text": piece})
        except LLMError as exc:
            yield sse_event({"type": "error", "message": str(exc)})
            return
        yield sse_event({"type": "done", "model": provider.model, "provider": provider.name})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",  # never store a half-finished answer
            "X-Accel-Buffering": "no",  # tell proxies (like nginx) not to hold the text back
        },
    )