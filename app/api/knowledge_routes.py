# app/api/knowledge_routes.py
#
# PURPOSE: Web addresses for looking inside CyberGPT's knowledge base.
#
#   GET /knowledge/stats    -> how much knowledge is loaded, per source and per domain
#   GET /knowledge/search   -> find the best-matching passages for a question
#
# WHY EXPOSE SEARCH AS ITS OWN ADDRESS (when the chat will use it internally later):
#   1. You can SEE what CyberGPT knows and test its search quality directly, without an AI
#      in the middle. If search finds the wrong thing, the AI's answer will be wrong too, so
#      this is the first place to look when something seems off.
#   2. Your teammates can try it from the automatic docs page (/docs) with no coding.
#
# These addresses are protected by the same password gate as everything else (app/security.py).

from __future__ import annotations

from collections import Counter
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.knowledge import DEFAULT_DATA_DIR, DOMAINS, SearchIndex, load_directory

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

SNIPPET_CHARS = 500


# ---------- the shapes of our replies ----------

class KnowledgeHit(BaseModel):
    id: str
    title: str
    source: str            # kev, attack or owasp
    tier: int              # 1 = authoritative, 2 = community
    domains: list[str]
    url: str               # the original page, for citing
    license: str
    as_of: str             # the date the source gives for this entry
    ref_id: str            # official ID such as CVE-2021-44228 or T1059 (empty if none)
    score: float
    exact_id: bool         # True = found by official ID rather than by keywords
    snippet: str           # the start of the passage (or all of it with full=true)
    text_length: int


class KnowledgeSearchResponse(BaseModel):
    query: str
    count: int
    ids_in_query: list[str]
    # IDs you asked about that are NOT in our loaded sources. This is NOT proof the ID does
    # not exist; it means "we cannot confirm it from what we have".
    ids_not_found: list[str]
    results: list[KnowledgeHit]


class KnowledgeStats(BaseModel):
    ready: bool            # False until you run scripts/ingest_knowledge.py
    total: int
    sources: dict[str, int]
    domains: dict[str, int]
    hint: str = ""


# ---------- the search index ----------

@lru_cache
def get_search_index() -> SearchIndex:
    """Loads the knowledge files and builds the search index ONCE, then reuses it.
    (Building takes under a second; doing it on every request would be wasteful.)
    This is a function so tests can swap in a tiny index with dependency_overrides."""
    return SearchIndex(load_directory(DEFAULT_DATA_DIR))


# ---------- the addresses ----------

@router.get("/stats", response_model=KnowledgeStats)
def knowledge_stats(index: SearchIndex = Depends(get_search_index)) -> KnowledgeStats:
    if len(index) == 0:
        return KnowledgeStats(
            ready=False, total=0, sources={}, domains={},
            hint="No knowledge loaded yet. Run: python scripts/ingest_knowledge.py",
        )
    return KnowledgeStats(
        ready=True,
        total=len(index),
        sources=dict(Counter(c.source for c in index.chunks)),
        domains=dict(Counter(d for c in index.chunks for d in c.domains)),
    )


@router.get("/search", response_model=KnowledgeSearchResponse)
def knowledge_search(
    # Query(...) lets us document each option and set limits; FastAPI rejects bad values with
    # a clear message (HTTP 422) before our code runs.
    q: str = Query(..., min_length=2, max_length=300, description="Your question or keywords"),
    domain: list[str] | None = Query(None, description=f"Limit to topic areas: {', '.join(DOMAINS)}"),
    source: list[str] | None = Query(None, description="Limit to sources: kev, attack, owasp"),
    limit: int = Query(5, ge=1, le=20, description="How many results"),
    full: bool = Query(False, description="Return each passage in full instead of a short snippet"),
    index: SearchIndex = Depends(get_search_index),
) -> KnowledgeSearchResponse:
    unknown = [d for d in (domain or []) if d not in DOMAINS]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown domain: {', '.join(unknown)}. Choose from: {', '.join(DOMAINS)}")

    result = index.search(q, limit=limit, domains=domain, sources=source)

    def snippet(text: str) -> str:
        if full or len(text) <= SNIPPET_CHARS:
            return text
        return text[:SNIPPET_CHARS].rsplit(" ", 1)[0] + " ..."

    return KnowledgeSearchResponse(
        query=q,
        count=len(result.hits),
        ids_in_query=result.ids_in_query,
        ids_not_found=result.ids_not_found,
        results=[
            KnowledgeHit(
                id=h.chunk.id, title=h.chunk.title, source=h.chunk.source, tier=h.chunk.tier,
                domains=list(h.chunk.domains), url=h.chunk.url, license=h.chunk.license,
                as_of=h.chunk.as_of, ref_id=h.chunk.ref_id, score=h.score, exact_id=h.exact_id,
                snippet=snippet(h.chunk.text), text_length=len(h.chunk.text),
            )
            for h in result.hits
        ],
    )