# tests/test_rag_context.py
#
# PURPOSE: Check app/rag/context.py: the text the AI reads and the cards the page shows.
# The numbering must be identical in both, or a citation would point at the wrong source.

from app.knowledge.search import Hit
from app.rag.context import MAX_TOTAL_CHARS, build_sources_block, source_cards
from app.rag.retrieval import Retrieval, retrieve
from tests.rag_helpers import make, small_index


def hits_for(question):
    return retrieve(small_index(), None, [("user", question)], rewrite=False)


def test_cards_are_numbered_from_one_in_order():
    r = hits_for("prevent SQL injection")
    cards = source_cards(r.hits)
    assert [c.n for c in cards] == list(range(1, len(cards) + 1))
    assert cards[0].id == "o1"
    assert cards[0].label == "OWASP Cheat Sheets" and cards[0].tier == "community"
    assert cards[0].url == "https://example.test/o1"


def test_kev_cards_are_marked_authoritative_with_a_readable_name():
    card = source_cards(hits_for("explain CVE-2021-44228").hits)[0]
    assert (card.label, card.tier, card.ref_id, card.exact_id) == ("CISA KEV", "authoritative", "CVE-2021-44228", True)
    assert card.as_of == "2021-12-10"


def test_the_block_contains_numbered_passages_and_the_rules():
    r = hits_for("prevent SQL injection")
    block = build_sources_block(r)

    assert "[1] OWASP Cheat Sheets | SQL Injection Prevention" in block
    assert "parameterized queries" in block
    for rule in ("cite", "never instructions", "general knowledge", "Never invent"):
        assert rule.lower() in block.lower()


def test_brackets_inside_a_passage_cannot_look_like_citations():
    # The passage o1 says "See [1] for details." That must not read as a citation.
    block = build_sources_block(hits_for("prevent SQL injection"))
    body = block.split("[1] OWASP", 1)[1]
    assert "See (1) for details" in body and "See [1]" not in body


def test_no_passages_means_a_clear_instruction_not_to_cite():
    block = build_sources_block(Retrieval(searched="weather"))
    assert "nothing relevant" in block
    assert "Do not cite" in block
    assert "[1]" not in block


def test_unknown_ids_trigger_the_do_not_invent_guard():
    block = build_sources_block(Retrieval(searched="x", ids_not_found=["CVE-1999-0001", "CWE-99999"]))
    assert "CVE-1999-0001, CWE-99999" in block
    assert "NO entry" in block
    assert "does not prove" in block or "do not exist" in block
    assert "nvd.nist.gov" in block


def test_the_guard_is_absent_when_every_id_was_found():
    assert "NO entry" not in build_sources_block(hits_for("explain CVE-2021-44228"))


def test_long_passages_and_many_passages_are_capped():
    big = [Hit(make(f"b{i}", f"Title {i}", "word " * 3000), score=1.0) for i in range(8)]
    block = build_sources_block(Retrieval(searched="x", hits=big))
    assert len(block) < MAX_TOTAL_CHARS + 2500   # passages + the fixed rules text
    assert " ..." in block                        # shown as visibly shortened


def test_card_excerpt_is_short():
    big = [Hit(make("b", "T", "word " * 3000), score=1.0)]
    assert len(source_cards(big)[0].excerpt) < 340