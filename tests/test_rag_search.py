# tests/test_rag_search.py
#
# PURPOSE: Check the Step 4 additions to the search engine: coverage, strength, the title
# bonus and the "chatty words" list.

from app.knowledge import SearchIndex
from app.knowledge.search import QUERY_FILLER
from tests.rag_helpers import make, small_index


def test_a_passage_covering_the_whole_question_has_full_coverage():
    hit = small_index().search("prevent SQL injection").hits[0]
    assert hit.chunk.id == "o1"
    assert hit.coverage == 1.0


def test_unknown_words_lower_coverage_instead_of_being_ignored():
    # "log4shell" appears nowhere. If it were ignored, the one known word would give
    # coverage 1.0, so an almost-unanswerable question would look like a perfect match.
    result = small_index().search("what is log4shell injection")
    assert result.hits, "the known word 'injection' should still match something"
    assert result.hits[0].coverage < 0.5


def test_a_question_made_only_of_unknown_words_finds_nothing():
    assert small_index().search("log4shell").hits == []


def test_a_better_covered_passage_beats_a_single_strong_word():
    index = SearchIndex([
        # Mentions "injection" many times but says nothing about "prevention".
        make("a", "Injection", "injection injection injection injection injection injection"),
        make("b", "Prevention of injection", "How to prevent injection with safe queries."),
        make("c", "Filler one", "unrelated words about networks"),
        make("d", "Filler two", "more unrelated words about cloud"),
    ])
    assert index.search("injection prevention").hits[0].chunk.id == "b"


def test_words_in_the_title_give_a_bonus():
    index = SearchIndex([
        make("body", "Misc notes", "kerberoasting is described here in the body text only"),
        make("title", "Kerberoasting", "kerberoasting is described here in the body text only"),
        make("f1", "Filler one", "other things"),
        make("f2", "Filler two", "more other things"),
    ])
    assert index.search("kerberoasting").hits[0].chunk.id == "title"


def test_strength_does_not_depend_on_how_big_the_knowledge_base_is():
    small = small_index()
    big = SearchIndex(list(small.chunks) + [make(f"x{i}", f"Filler {i}", f"filler words number {i}") for i in range(200)])

    a = small.search("prevent SQL injection").hits[0].strength
    b = big.search("prevent SQL injection").hits[0].strength
    assert a > 0.4 and b > 0.4  # both clearly "strong"
    assert abs(a - b) < 1.0     # and in the same range (raw scores differ far more)


def test_chatty_words_are_ignored_in_questions():
    index = small_index()
    plain = index.search("SQL injection").hits[0]
    chatty = index.search("please explain SQL injection like I'm a beginner").hits[0]
    assert chatty.chunk.id == plain.chunk.id
    assert chatty.coverage == 1.0
    assert {"explain", "beginner", "hello", "thanks"} <= QUERY_FILLER


def test_a_greeting_alone_finds_nothing():
    assert small_index().search("hi").hits == []
    assert small_index().search("thanks!").hits == []


def test_exact_id_hits_have_full_coverage_and_strength():
    hit = small_index().search("CVE-2021-44228").hits[0]
    assert hit.exact_id and hit.coverage == 1.0 and hit.strength >= 1000