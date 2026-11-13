# app/rag/retrieval.py
#
# PURPOSE: Decide WHICH passages from the knowledge base to give the AI for a question.
#
# THE STEPS:
#   1. Work out what to search for (the latest question; if it is a very short follow-up, the
#      question before it too; very long pasted evidence is cut down to its first part).
#   2. Optionally ask the AI for better keywords (rewrite.py) and search with those as well.
#   3. Merge the results, then DROP weak matches. A search engine always returns "the best
#      of what it has", even when nothing is relevant (ask about cooking and it still hands
#      back some passage). Showing that as a source would be misleading, so we filter.
#
# THE FILTER IS A FIRST LINE OF DEFENCE ONLY. Keyword matching cannot tell "mentions the same
# word" from "answers the question", so some off-topic passages still get through. That is
# why the AI is also told to use only what truly helps, and why the page shows only the
# sources the answer actually cited.

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from app.knowledge.search import ID_PATTERN, Hit, SearchIndex, tokenize
from app.llm import LLMProvider
from app.rag.rewrite import rewrite_query

log = logging.getLogger("cybergpt.rag")

# ---- the relevance filter (tuned on real questions; see tests/test_rag_retrieval.py) ----
# On 38 real questions (25 on-topic, 13 off-topic) these values dropped about two thirds of
# the off-topic ones and kept every on-topic one. The rest is left to the AI (see above).
MIN_COVERAGE = 0.40   # a passage must contain at least 40% of the question's weighted words
MIN_STRENGTH = 0.40   # ...and match strongly enough (Hit.strength, independent of corpus size)
RELATIVE_CUTOFF = 0.45  # ...and score at least 45% of the best passage, so we do not pad

# Very long messages (pasted scan output) are searched by their opening only; official IDs
# are still taken from the WHOLE message so a pasted CVE list is not lost.
MAX_SEARCH_CHARS = 400

# A follow-up this short MAY lean on the previous question ("and how do I fix it?")...
SHORT_FOLLOWUP_WORDS = 4

# ...but only if it also LOOKS like a follow-up. A short message with no such cue ("tell me a
# joke", "what is XSS") is a brand-new question, and borrowing the previous question for it would
# drag in the old topic. (A real bug found in testing: after asking about CVE-1999-0001, the
# question "tell me a joke" was wrongly reported as being about CVE-1999-0001.)
FOLLOWUP_CUES = re.compile(
    r"\b(it|its|this|that|these|those|they|them|one|ones|above|earlier|previous|same|"
    r"also|more|another|else|instead)\b|^\s*(and|but|so|then|what about|how about)\b",
    re.IGNORECASE,
)

# The extra "ask the AI for better keywords" call costs time and free-quota tokens, so we make
# it only when the plain search did NOT already find a well-matching passage.
GOOD_ENOUGH_COVERAGE = 0.70


@dataclass(frozen=True)
class Retrieval:
    searched: str                 # the exact text we searched with (shown to the user)
    hits: list[Hit] = field(default_factory=list)   # the passages to use, best first
    ids_not_found: list[str] = field(default_factory=list)  # IDs the user named that we lack
    keywords: str = ""            # what the AI suggested (empty if rewriting was off or failed)


def search_text_for(history: list[tuple[str, str]]) -> str:
    """Build the text to search with from the conversation (role, text) pairs."""
    user_turns = [text for role, text in history if role == "user"]
    if not user_turns:
        return ""

    latest = user_turns[-1]
    text = latest[:MAX_SEARCH_CHARS]

    # A short follow-up borrows the previous question so the topic is not lost.
    if (
        len(user_turns) > 1
        and len(tokenize(latest)) <= SHORT_FOLLOWUP_WORDS
        and FOLLOWUP_CUES.search(latest)
    ):
        text = f"{user_turns[-2][:MAX_SEARCH_CHARS]} {text}"

    # Official IDs anywhere in the latest message always count, even past the cut-off.
    for found in ID_PATTERN.findall(latest):
        if found.upper() not in text.upper():
            text += f" {found}"
    return text.strip()


def select_hits(hits: list[Hit], max_sources: int) -> list[Hit]:
    """Keep exact-ID matches plus the keyword matches that pass the relevance filter."""
    exact = [h for h in hits if h.exact_id]
    keyword = sorted((h for h in hits if not h.exact_id), key=lambda h: h.score, reverse=True)

    kept: list[Hit] = []

    def strong_enough(h: Hit) -> bool:
        return h.coverage >= MIN_COVERAGE and h.strength >= MIN_STRENGTH

    if keyword and strong_enough(keyword[0]):
        best = keyword[0].score
        kept = [h for h in keyword if strong_enough(h) and h.score >= RELATIVE_CUTOFF * best]
    return (exact + kept)[:max_sources]


def retrieve(
    index: SearchIndex,
    provider: LLMProvider | None,
    history: list[tuple[str, str]],
    *,
    rewrite: bool = True,
    max_sources: int = 4,
) -> Retrieval:
    """Find the passages for the latest question. NEVER raises: if anything goes wrong the
    chat simply continues without sources."""
    max_sources = max(1, min(max_sources, 8))
    try:
        text = search_text_for(history)
        if not text or len(index) == 0:
            return Retrieval(searched=text)

        # Search with the user's own words first.
        # We fetch extra results so the filter has something to choose from.
        pool = max_sources * 3
        first = index.search(text, limit=pool)
        merged = {h.chunk.id: h for h in first.hits}

        # Then, if allowed, with the AI's keywords. Skipped when the question already holds
        # an official ID (an exact lookup; keywords would only add noise) and when the plain
        # search already found a passage that covers the question well (saves a whole AI call).
        keywords = ""
        good_already = any(
            h.coverage >= GOOD_ENOUGH_COVERAGE
            for h in select_hits(first.hits, max_sources)
            if not h.exact_id
        )
        if rewrite and provider is not None and not first.ids_in_query and not good_already:
            keywords = rewrite_query(provider, history) or ""
            if keywords:
                for h in index.search(keywords, limit=pool).hits:
                    # If both searches found it, keep whichever scored it higher.
                    if h.chunk.id not in merged or h.score > merged[h.chunk.id].score:
                        merged[h.chunk.id] = h

        # "Not in our sources" warnings are only for IDs in the user's LATEST message. An ID
        # from an earlier question (or borrowed for a follow-up) must not reappear forever.
        latest = [t for role, t in history if role == "user"][-1]
        latest_ids: list[str] = []
        for found in ID_PATTERN.findall(latest):
            if found.upper() not in latest_ids:
                latest_ids.append(found.upper())
        ids_not_found = [i for i in latest_ids if not index.lookup(i)]

        chosen = select_hits(list(merged.values()), max_sources)
        return Retrieval(
            searched=text,
            hits=chosen,
            ids_not_found=ids_not_found,
            keywords=keywords,
        )
    except Exception:  # noqa: BLE001 - retrieval is a bonus; it must never break chat
        log.exception("retrieval failed; continuing without sources")
        return Retrieval(searched="")