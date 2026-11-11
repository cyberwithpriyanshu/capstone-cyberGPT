# tests/test_rag_rewrite.py
#
# PURPOSE: Check app/rag/rewrite.py: the step that asks the AI for better search keywords.
# The most important checks are the SAFETY ones: the AI's suggestion is cleaned, identifiers
# it might invent are removed, and any failure is swallowed so chat keeps working.

import pytest

from app.rag.rewrite import MAX_KEYWORDS_CHARS, clean_keywords, rewrite_query
from tests.rag_helpers import ScriptedProvider


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Apache Log4j2 JNDI remote code execution", "Apache Log4j2 JNDI remote code execution"),
        ("Keywords: SQL injection prevention", "SQL injection prevention"),
        ('"SQL injection prevention"', "SQL injection prevention"),
        ("`xss output encoding`", "xss output encoding"),
        # only the first line counts; models like to add an explanation underneath
        ("kerberoasting service tickets\nThis is because ...", "kerberoasting service tickets"),
        ("\n\n  pass the hash NTLM  \n", "pass the hash NTLM"),
        # odd symbols are stripped
        ("sql <script> injection; DROP TABLE", "sql script injection DROP TABLE"),
    ],
)
def test_clean_keywords_tidies_the_reply(raw, expected):
    assert clean_keywords(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "NONE", "none - small talk", "\n\n"])
def test_nothing_usable_returns_none(raw):
    assert clean_keywords(raw) is None


def test_identifiers_the_ai_invents_are_removed():
    # If this leaked through, a made-up CVE would be looked up as if the USER had asked for it.
    cleaned = clean_keywords("log4j remote code execution CVE-2021-99999 CWE-79 T1059.001 S0002")
    assert cleaned == "log4j remote code execution"
    assert clean_keywords("CVE-2021-44228") is None  # nothing left once the ID is gone


def test_very_long_output_is_cut():
    assert len(clean_keywords("word " * 500)) <= MAX_KEYWORDS_CHARS


def test_rewrite_query_returns_the_cleaned_keywords():
    provider = ScriptedProvider(keywords="Keywords: Apache Log4j2 remote code execution")
    result = rewrite_query(provider, [("user", "what is log4shell")])
    assert result == "Apache Log4j2 remote code execution"


def test_rewrite_prompt_contains_the_conversation_and_asks_for_keywords():
    provider = ScriptedProvider(keywords="sql injection")
    rewrite_query(provider, [("user", "what is sqli"), ("assistant", "A flaw..."), ("user", "how do I fix it?")])

    system, user = provider.requests[0]
    assert system.role == "system" and "keyword" in system.content.lower()
    assert "how do I fix it?" in user.content
    assert "what is sqli" in user.content  # earlier turns are included for follow-ups


def test_only_the_last_few_turns_and_short_turns_are_sent():
    provider = ScriptedProvider(keywords="x")
    history = [("user", f"message-{i} " + "a" * 2000) for i in range(10)]
    rewrite_query(provider, history)

    sent = provider.requests[0][1].content
    assert "message-9" in sent and "message-0" not in sent
    assert len(sent) < 4 * 700 + 300


@pytest.mark.parametrize("exc", [RuntimeError("boom"), TimeoutError("slow"), ValueError("bad")])
def test_any_failure_returns_none_instead_of_raising(exc):
    class Broken(ScriptedProvider):
        def chat(self, messages, **kwargs):
            raise exc

    assert rewrite_query(Broken(), [("user", "hello?")]) is None