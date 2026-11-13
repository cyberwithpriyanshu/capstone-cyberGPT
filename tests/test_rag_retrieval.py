# tests/test_rag_retrieval.py
#
# PURPOSE: Check app/rag/retrieval.py: what we search for, which results survive the relevance
# filter, how the AI's keywords are merged in, and that nothing here can ever crash a chat.

import pytest

from app.knowledge import SearchIndex
from app.knowledge.search import Hit
from app.rag.retrieval import (
    MAX_SEARCH_CHARS,
    retrieve,
    search_text_for,
    select_hits,
)
from tests.rag_helpers import ScriptedProvider, make, small_index


# ---------------------------------------------------------------- what we search for

def test_search_text_is_the_latest_user_message():
    history = [("user", "what is XSS"), ("assistant", "..."), ("user", "how do I prevent SQL injection in my php login form")]
    assert search_text_for(history) == "how do I prevent SQL injection in my php login form"


def test_a_short_follow_up_borrows_the_previous_question():
    history = [("user", "explain SQL injection prevention"), ("assistant", "..."), ("user", "and examples?")]
    text = search_text_for(history)
    assert "SQL injection prevention" in text and "examples" in text


def test_a_short_but_new_question_does_not_borrow_the_previous_one():
    # Regression: after "What is CVE-1999-0001?", the new question "tell me a joke" was
    # searched together with the old CVE and wrongly reported as being about it.
    history = [("user", "What is CVE-1999-0001?"), ("assistant", "..."), ("user", "tell me a joke")]
    assert search_text_for(history) == "tell me a joke"
    r = retrieve(small_index(), None, history, rewrite=False)
    assert r.ids_not_found == []


@pytest.mark.parametrize("followup", ["how do I fix it?", "and examples?", "what about this one", "why does that work"])
def test_follow_up_cues_borrow_the_previous_question(followup):
    history = [("user", "explain SQL injection prevention"), ("assistant", "..."), ("user", followup)]
    assert "SQL injection prevention" in search_text_for(history)


def test_a_long_message_is_cut_but_ids_further_down_are_kept():
    pasted = "x" * (MAX_SEARCH_CHARS + 500) + " also check CVE-2021-44228 please"
    text = search_text_for([("user", pasted)])
    assert len(text) < MAX_SEARCH_CHARS + 40
    assert "CVE-2021-44228" in text


def test_no_user_message_means_nothing_to_search():
    assert search_text_for([]) == ""
    assert search_text_for([("assistant", "hi")]) == ""


# ---------------------------------------------------------------- the relevance filter

def hit(id, score, coverage, strength, exact=False):
    return Hit(make(id, id, "text"), score=score, exact_id=exact, coverage=coverage, strength=strength)


def test_weak_matches_are_dropped():
    assert select_hits([hit("a", 9, 0.30, 0.9)], 4) == []   # covers too little of the question
    assert select_hits([hit("a", 9, 0.90, 0.2)], 4) == []   # matches too weakly


def test_when_the_best_match_is_weak_nothing_is_kept_even_if_others_look_ok():
    assert select_hits([hit("a", 9, 0.3, 0.9), hit("b", 8, 0.9, 0.9)], 4) == []


def test_weak_extras_are_not_used_to_pad_the_list():
    kept = select_hits([hit("best", 20, 1, 2), hit("close", 15, 1, 2), hit("far", 5, 1, 2)], 4)
    assert [h.chunk.id for h in kept] == ["best", "close"]


def test_exact_id_hits_always_come_first_and_always_survive():
    kept = select_hits([hit("kw", 20, 1, 2), hit("id", 1000, 1, 1000, exact=True)], 4)
    assert [h.chunk.id for h in kept] == ["id", "kw"]
    assert [h.chunk.id for h in select_hits([hit("id", 1000, 1, 1000, exact=True)], 4)] == ["id"]


def test_the_limit_is_respected():
    many = [hit(f"h{i}", 20, 1, 2) for i in range(10)]
    assert len(select_hits(many, 3)) == 3


# ---------------------------------------------------------------- retrieve()

def test_retrieve_finds_the_right_passage():
    r = retrieve(small_index(), None, [("user", "how do I prevent SQL injection?")], rewrite=False)
    assert r.hits and r.hits[0].chunk.id == "o1"
    assert r.keywords == ""


def test_off_topic_questions_find_nothing():
    r = retrieve(small_index(), None, [("user", "what is the weather in paris")], rewrite=False)
    assert r.hits == []


def test_an_unknown_id_is_reported_not_guessed():
    r = retrieve(small_index(), None, [("user", "tell me about CVE-1999-0001")], rewrite=False)
    assert r.ids_not_found == ["CVE-1999-0001"]
    assert not any(h.chunk.ref_id == "CVE-1999-0001" for h in r.hits)


def test_a_known_id_is_an_exact_hit():
    r = retrieve(small_index(), None, [("user", "explain CVE-2021-44228")], rewrite=False)
    assert r.hits[0].chunk.id == "k1" and r.hits[0].exact_id
    assert r.ids_not_found == []


def test_rewriting_fixes_a_question_whose_words_are_not_in_the_sources():
    index, history = small_index(), [("user", "what is log4shell")]

    # Without the AI's help: "log4shell" matches nothing.
    assert retrieve(index, None, history, rewrite=False).hits == []

    # With it: the AI supplies the words the sources actually use.
    provider = ScriptedProvider(keywords="Apache Log4j2 JNDI remote code execution")
    r = retrieve(index, provider, history, rewrite=True)
    assert r.hits and r.hits[0].chunk.id == "k1"
    assert r.keywords == "Apache Log4j2 JNDI remote code execution"


def test_invented_ids_in_the_rewrite_cannot_pull_in_a_passage():
    provider = ScriptedProvider(keywords="Apache Log4j2 CVE-2021-44228")
    r = retrieve(small_index(), provider, [("user", "what is log4shell")], rewrite=True)
    # The ID was stripped, so this was found by WORDS (not flagged as an exact-ID match).
    assert r.hits and not r.hits[0].exact_id


def test_the_rewrite_call_is_skipped_when_the_question_already_has_an_id():
    provider = ScriptedProvider(keywords="irrelevant")
    retrieve(small_index(), provider, [("user", "explain CVE-2021-44228")], rewrite=True)
    assert provider.requests == []


def test_rewrite_can_be_switched_off():
    provider = ScriptedProvider(keywords="Apache Log4j2")
    retrieve(small_index(), provider, [("user", "what is log4shell")], rewrite=False)
    assert provider.requests == []


def test_a_broken_rewrite_falls_back_to_the_plain_search():
    provider = ScriptedProvider(fail_rewrite=True)
    # "log4shell" finds nothing on the first try, so the rewrite IS attempted (and fails).
    r = retrieve(small_index(), provider, [("user", "what is log4shell")], rewrite=True)
    assert len(provider.requests) == 1          # it was tried
    assert r.hits == [] and r.keywords == ""    # and chat carries on without it


def test_the_rewrite_call_is_skipped_when_the_plain_search_is_already_good():
    provider = ScriptedProvider(keywords="should not be asked")
    r = retrieve(small_index(), provider, [("user", "how do I prevent SQL injection")], rewrite=True)
    assert r.hits and r.hits[0].chunk.id == "o1"
    assert provider.requests == []              # saved one AI call (and its free-quota tokens)


def test_an_empty_knowledge_base_returns_nothing_and_does_not_crash():
    r = retrieve(SearchIndex([]), None, [("user", "sql injection")], rewrite=False)
    assert r.hits == [] and r.ids_not_found == []


def test_retrieve_never_raises_even_if_search_blows_up():
    class Exploding:
        chunks = [1]
        def __len__(self): return 1
        def search(self, *a, **k): raise RuntimeError("index corrupted")

    r = retrieve(Exploding(), None, [("user", "sql injection")], rewrite=False)
    assert r.hits == []


@pytest.mark.parametrize("n", [0, -3, 99])
def test_max_sources_is_kept_in_a_sane_range(n):
    r = retrieve(small_index(), None, [("user", "prevention")], rewrite=False, max_sources=n)
    assert 0 <= len(r.hits) <= 8


def test_a_not_found_warning_belongs_to_the_latest_message_only():
    # The old CVE is borrowed into the SEARCH for the follow-up (so the topic is kept), but the
    # "cannot confirm this ID" warning is only about IDs the user typed in their latest message.
    history = [("user", "What is CVE-1999-0001?"), ("assistant", "..."), ("user", "how do I fix it?")]
    r = retrieve(small_index(), None, history, rewrite=False)
    assert "CVE-1999-0001" in r.searched
    assert r.ids_not_found == []

    again = retrieve(small_index(), None, history[:-1] + [("user", "and what is CVE-1999-0002?")], rewrite=False)
    assert again.ids_not_found == ["CVE-1999-0002"]