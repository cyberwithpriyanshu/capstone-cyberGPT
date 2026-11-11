# tests/rag_helpers.py
#
# PURPOSE: Small shared helpers for the Step 4 tests (a made-up knowledge base and a "pretend
# AI" that behaves differently for the keyword-rewrite call than for the real answer).
# This file is not a test itself (its name does not start with test_).

from app.knowledge import Chunk, SearchIndex
from app.llm import ChatMessage, LLMProvider
from app.llm.base import LLMResponse, ProviderHealth


def make(id, title, text, source="owasp", ref_id="", tier=2, as_of="2026-01-01"):
    return Chunk(
        id=id, source=source, tier=tier, title=title, text=text, url=f"https://example.test/{id}",
        license="test-license", domains=("web-app",), as_of=as_of, ref_id=ref_id,
    )


def small_index() -> SearchIndex:
    """Six passages. Enough variety that the ranking and the relevance filter have work to do."""
    return SearchIndex([
        make("o1", "SQL Injection Prevention",
             "Use parameterized queries (prepared statements) to prevent SQL injection. "
             "Never build a query by joining user input into the text. See [1] for details."),
        make("o2", "Cross Site Scripting Prevention",
             "Encode untrusted output for the right context to prevent cross-site scripting."),
        make("o3", "Docker Security", "Run containers as a non-root user and drop capabilities."),
        make("k1", "CVE-2021-44228 - Apache Log4j2 Remote Code Execution Vulnerability",
             "Apache Log4j2 allows remote code execution through JNDI lookups in log messages.",
             source="kev", ref_id="CVE-2021-44228", tier=1, as_of="2021-12-10"),
        make("a1", "T1110 Brute Force",
             "Adversaries guess passwords to gain access, for example against SSH or RDP.",
             source="attack", ref_id="T1110", tier=1),
        make("a2", "S0367 Emotet", "Emotet is a banking trojan spread by phishing email.",
             source="attack", ref_id="S0367", tier=1),
    ])


class ScriptedProvider(LLMProvider):
    """A pretend AI with two behaviours: it answers the keyword-rewrite request with
    `keywords`, and every other request with `answer`. It records every request it receives
    in `.requests` so a test can look at exactly what the AI was sent."""

    name = "scripted"
    model = "scripted-model"  # the streaming route reports these two in its final message

    def __init__(self, keywords="", answer="Pretend answer [1].", fail_rewrite=False):
        self.keywords = keywords
        self.answer = answer
        self.fail_rewrite = fail_rewrite
        self.requests: list[list[ChatMessage]] = []

    @staticmethod
    def is_rewrite(messages) -> bool:
        return messages[0].content.startswith("You write search keywords")

    def chat(self, messages, *, temperature=None, max_tokens=None) -> LLMResponse:
        self.requests.append(list(messages))
        if self.is_rewrite(messages):
            if self.fail_rewrite:
                raise RuntimeError("rewrite exploded")
            return LLMResponse(self.keywords, "scripted-model", "scripted")
        return LLMResponse(self.answer, "scripted-model", "scripted")

    def chat_stream(self, messages, *, temperature=None, max_tokens=None):
        self.requests.append(list(messages))
        yield "Pretend "
        yield "answer [1]."

    def health(self) -> ProviderHealth:
        return ProviderHealth(ok=True, provider="scripted", model="scripted-model", detail="ok")

    @property
    def answer_requests(self):
        return [r for r in self.requests if not self.is_rewrite(r)]