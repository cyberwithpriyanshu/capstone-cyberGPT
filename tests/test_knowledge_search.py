# tests/test_knowledge_search.py
#
# PURPOSE: Check the storage format and the search engine, using a small made-up knowledge base
# where we know exactly which result SHOULD come first.

import pytest

from app.knowledge import Chunk, SearchIndex
from app.knowledge.search import stem, tokenize
from app.knowledge.store import load_chunks, load_directory, save_chunks


def make(id, title, text, source="owasp", domains=("web-app",), ref_id="", tier=2):
    return Chunk(id=id, source=source, tier=tier, title=title, text=text, url=f"https://x/{id}",
                 license="test", domains=domains, as_of="2026-01-01", ref_id=ref_id)


@pytest.fixture
def index():
    return SearchIndex([
        make("o1", "SQL Injection Prevention", "Use parameterized queries to stop SQL injection attacks."),
        make("o2", "Cross Site Scripting", "Encode output to prevent cross-site scripting in the browser."),
        make("o3", "Kubernetes Security", "Keep secrets out of images and rotate Kubernetes secrets.",
             domains=("defense", "cloud")),
        make("k1", "CVE-2021-44228 - Log4j", "Remote code execution in Log4j via JNDI lookups.",
             source="kev", domains=("vuln-intel",), ref_id="CVE-2021-44228", tier=1),
        make("a1", "T1059.001 PowerShell", "Adversaries abuse PowerShell for execution.",
             source="attack", domains=("threat-behavior",), ref_id="T1059.001", tier=1),
        make("a2", "S0367 Emotet", "Emotet is a banking trojan spread by phishing.",
             source="attack", domains=("malware", "threat-behavior"), ref_id="S0367", tier=1),
    ])


# ---------------------------------------------------------------- text handling

def test_tokenize_lowercases_and_drops_filler_words():
    assert tokenize("How do I Prevent the SQL Injection?") == ["prevent", "sql", "injection"]


def test_stem_removes_simple_plurals_only():
    assert stem("attacks") == "attack"
    assert stem("vulnerabilities") == "vulnerability"
    assert stem("class") == "class"      # "ss" is not a plural
    assert stem("status") == "status"    # neither is "us"
    assert stem("xss") == "xss"          # too short to touch


# ---------------------------------------------------------------- ranking

def test_the_most_relevant_passage_ranks_first(index):
    result = index.search("how do I stop sql injection")

    assert result.hits[0].chunk.id == "o1"


def test_plurals_still_match(index):
    assert index.search("injection attack").hits[0].chunk.id == "o1"
    assert index.search("kubernetes secret").hits[0].chunk.id == "o3"


def test_unrelated_words_find_nothing(index):
    assert index.search("quantum banana").hits == []


def test_limit_is_respected(index):
    assert len(index.search("security attacks execution code", limit=2).hits) <= 2


# ---------------------------------------------------------------- filters

def test_domain_filter(index):
    result = index.search("secrets", domains=["cloud"])
    assert [h.chunk.id for h in result.hits] == ["o3"]

    assert index.search("secrets", domains=["malware"]).hits == []


def test_source_filter(index):
    result = index.search("execution", sources=["attack"])

    assert result.hits and all(h.chunk.source == "attack" for h in result.hits)


# ---------------------------------------------------------------- exact IDs

def test_exact_id_comes_first_and_is_marked(index):
    result = index.search("tell me about cve-2021-44228")   # lower case still works

    assert result.hits[0].chunk.id == "k1"
    assert result.hits[0].exact_id is True
    assert result.ids_in_query == ["CVE-2021-44228"]
    assert result.ids_not_found == []


def test_attack_ids_work_for_techniques_and_software(index):
    assert index.search("T1059.001").hits[0].chunk.id == "a1"
    assert index.search("what is S0367").hits[0].chunk.id == "a2"


def test_unknown_id_is_reported_and_no_lookalike_is_returned(index):
    # THE guardrail: asking about an ID we do not have must NOT return a similar-looking
    # number as if it were the answer.
    result = index.search("CVE-2021-44229")   # one digit off from the real one

    assert result.ids_not_found == ["CVE-2021-44229"]
    assert result.hits == []


def test_mixed_question_reports_only_the_missing_ids(index):
    result = index.search("compare CVE-2021-44228 with CVE-2099-0001")

    assert result.ids_in_query == ["CVE-2021-44228", "CVE-2099-0001"]
    assert result.ids_not_found == ["CVE-2099-0001"]
    assert result.hits[0].chunk.id == "k1"


def test_lookup_by_id(index):
    assert [c.id for c in index.lookup("cve-2021-44228")] == ["k1"]
    assert index.lookup("CVE-1999-9999") == []


def test_empty_index_never_crashes():
    empty = SearchIndex([])

    assert len(empty) == 0
    assert empty.search("anything").hits == []
    assert empty.search("CVE-2021-44228").ids_not_found == ["CVE-2021-44228"]


# ---------------------------------------------------------------- storage

def test_chunks_survive_a_save_and_load(tmp_path):
    original = [make("o1", "Title with accents é", "Body text", ref_id="X1"),
                make("o2", "Second", "More text", domains=("cloud", "defense"))]

    save_chunks(tmp_path / "test.jsonl", original)
    loaded = load_chunks(tmp_path / "test.jsonl")

    assert loaded == original
    assert (tmp_path / "test.jsonl").read_text(encoding="utf-8").count("\n") == 2  # one line per chunk


def test_load_directory_combines_files_and_tolerates_a_missing_folder(tmp_path):
    save_chunks(tmp_path / "a.jsonl", [make("o1", "A", "text")])
    save_chunks(tmp_path / "b.jsonl", [make("o2", "B", "text")])

    assert [c.id for c in load_directory(tmp_path)] == ["o1", "o2"]
    assert load_directory(tmp_path / "does-not-exist") == []


def test_damaged_file_gives_a_clear_error(tmp_path):
    (tmp_path / "bad.jsonl").write_text('{"id": "x"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="bad.jsonl, line 1"):
        load_chunks(tmp_path / "bad.jsonl")