# app/rag/rewrite.py
#
# PURPOSE: Turn a chatty question into good SEARCH KEYWORDS by asking the AI itself.
#
# WHY: Our search matches words. People ask about "log4shell", but our sources call it
# "Apache Log4j2 ... Remote Code Execution". The words never meet, so plain search finds
# nothing. The AI knows both names, so we ask it: "what would a document about this be
# called?" It also resolves follow-ups: after a question about SQL injection, "how do I fix
# it?" becomes "SQL injection prevention parameterized queries".
#
# SAFETY RULES (this is the important part):
#   1. The AI's keywords are only used to SEARCH. They never reach the user as facts.
#   2. We REMOVE any CVE / CWE / ATT&CK identifiers from what the AI suggests. Otherwise a
#      model that invents "CVE-2021-99999" would make us fetch (or wrongly report as
#      missing) an ID the user never asked about. Identifiers come only from the user's
#      own words (the search code already handles those exactly).
#   3. If the AI fails, answers badly, or says nothing, we return None and the search just
#      uses the original question. Rewriting can only ever HELP; it can never break chat.

from __future__ import annotations

import logging
import re

from app.knowledge.search import ID_PATTERN
from app.llm import ChatMessage, LLMProvider

log = logging.getLogger("cybergpt.rag")

REWRITE_INSTRUCTIONS = """\
You write search keywords for a keyword search engine. The search engine holds: the CISA \
Known Exploited Vulnerabilities list, MITRE ATT&CK (techniques, malware, tools, mitigations) \
and the OWASP Cheat Sheet Series.

Read the conversation and write the keywords that best find passages answering the user's \
LAST message.

Rules:
- Output ONE line of 3 to 12 keywords or short phrases, separated by spaces. No sentence, no \
explanation, no quotes, no labels.
- Use the standard names such as a document would use: "Log4Shell" becomes "Apache Log4j2 \
JNDI remote code execution"; "pass the hash" stays "pass the hash NTLM".
- If the last message is a follow-up ("how do I fix it?"), use the earlier messages to name \
the topic.
- Never write CVE, CWE or ATT&CK identifiers (like CVE-2021-1234 or T1059).
- If the message is small talk or has nothing to do with security, output exactly: NONE
"""

# How much of the conversation the rewriter sees. Short on purpose: it is a tiny task.
MAX_TURNS = 4
MAX_CHARS_PER_TURN = 500
MAX_KEYWORDS_CHARS = 200


def clean_keywords(raw: str) -> str | None:
    """Make the AI's reply safe to search with. Returns None if nothing usable is left."""
    # Only the first non-empty line (models sometimes add an explanation below).
    line = next((ln.strip() for ln in raw.splitlines() if ln.strip()), "")

    # Drop a leading label such as "Keywords:" and surrounding quotes/backticks.
    line = re.sub(r"^(search\s+)?(keywords?|query)\s*[:\-]\s*", "", line, flags=re.IGNORECASE)
    line = line.strip(" \t\"'`*")

    if not line or line.upper().startswith("NONE"):
        return None

    # Rule 2 above: identifiers are removed.
    line = ID_PATTERN.sub(" ", line)

    # Keep letters, digits and a few harmless symbols. This also removes anything odd.
    line = re.sub(r"[^A-Za-z0-9 .+#/_-]", " ", line)
    line = re.sub(r"\s+", " ", line).strip()[:MAX_KEYWORDS_CHARS]

    return line or None


def rewrite_query(provider: LLMProvider, history: list[tuple[str, str]]) -> str | None:
    """Ask the AI for search keywords. `history` is a list of (role, text) pairs, oldest
    first, ending with the user's latest message. Never raises."""
    recent = history[-MAX_TURNS:]
    transcript = "\n".join(
        f"{role.upper()}: {text[:MAX_CHARS_PER_TURN]}" for role, text in recent
    )

    messages = [
        ChatMessage("system", REWRITE_INSTRUCTIONS),
        ChatMessage("user", f"Conversation:\n{transcript}\n\nKeywords for the last USER message:"),
    ]

    try:
        # temperature 0 = as predictable as possible. The token limit leaves room for models
        # that "think" first (their thinking counts towards it); the answer itself is tiny.
        reply = provider.chat(messages, temperature=0.0, max_tokens=400)
    except Exception as exc:  # noqa: BLE001 - rewriting is optional, so ANY failure is fine
        log.warning("query rewrite skipped: %s", exc)
        return None

    return clean_keywords(reply.content or "")