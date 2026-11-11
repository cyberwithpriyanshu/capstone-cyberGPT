# app/knowledge/search.py
#
# PURPOSE: Finds the chunks that best match a question, using plain keyword search.
#
# THE METHOD, "BM25" (the classic ranking formula behind most search engines):
#   - A word that is RARE across all chunks is a strong clue ("log4shell" tells you a lot,
#     "the" tells you nothing).
#   - A word appearing several times in a chunk counts more, but with diminishing returns.
#   - Long chunks are slightly penalised so they don't win just by containing more words.
#
# WHY KEYWORDS FIRST (and embeddings later): security questions are full of exact terms,
# like CVE numbers, port numbers, tool names and "T1059". Keyword search is excellent at
# exact terms, needs no model download, uses almost no memory, and we can explain exactly
# why a result ranked high. In Step 4 we may ADD meaning-based search on top of it.
#
# NEW IN STEP 4 (ranking by "how much of the question does this passage cover?"):
#   Plain BM25 can rank a passage first because it matches ONE rare word very strongly while
#   ignoring the rest of the question. So each result now also gets a "coverage" number, the
#   share of the question's (weighted) words that the passage contains. Ranking multiplies the
#   BM25 score by that coverage, and gives a bonus when the words appear in the passage TITLE.
#   Step 4 also uses coverage to decide when NOTHING matches well enough to be worth showing.
#
# SECOND METHOD: exact ID lookup. If the question contains "CVE-2021-44228" or "T1059", we
# return the chunk that officially has that ID FIRST. If we do not have that ID we say so
# (ids_not_found) and we never return a look-alike number as if it were the answer.

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from app.knowledge.models import Chunk

# Words too common to help ranking.
STOPWORDS = frozenset(
    "a an and are as at be but by do does for from how i if in into is it its of on or "
    "so than that the their then there these they this to was what when where which who "
    "why will with you your can could should would about not no".split()
)

# Chatty words people put in questions ("explain ... like I'm a beginner"). They say nothing
# about the TOPIC, but without this list they count as words the passage "failed to match" and
# drag every coverage number down. Used for QUESTIONS only; the stored passages are untouched.
QUERY_FILLER = frozenset(
    "explain describe tell show give please help like beginner simple simply basic basics "
    "m ll ve re s t d me my we us our matter matters good best way ways work works working "
    "write make need want know understand mean means used use using look looks example "
    "examples thing things any some also just really very much many more most "
    "hi hello hey thanks thank lot lots ok okay yes bye".split()
)

# The official identifiers we can look up exactly (case-insensitive).
#   CVE-2021-44228   CWE-79   T1059 / T1059.001 (technique)   S0061 (software)   M1038 (mitigation)
ID_PATTERN = re.compile(
    r"\b(CVE-\d{4}-\d{4,}|CWE-\d+|T\d{4}(?:\.\d{3})?|S\d{4}|M\d{4})\b", re.IGNORECASE
)


def stem(word: str) -> str:
    """A very light 'plural remover' so 'attacks' matches 'attack' and
    'vulnerabilities' matches 'vulnerability'. (Not a full stemmer: simple on purpose.)"""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith("sses"):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def tokenize(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return [stem(w) for w in words if w not in STOPWORDS]


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float
    exact_id: bool = False  # True when it was found by an official ID, not by keywords
    # NEW in Step 4: 0..1, the share of the question's weighted words this passage contains.
    # (An exact-ID hit counts as 1.0.) Step 4 uses it to drop weak matches.
    coverage: float = 0.0
    # NEW in Step 4: the score divided by the total weight of the question's words. Unlike
    # `score` it does not grow or shrink with the size of the knowledge base or the length of
    # the question, so one cut-off value works everywhere ("how strong is this match, really?").
    strength: float = 0.0


@dataclass(frozen=True)
class SearchResult:
    hits: list[Hit]
    # Official IDs that appeared in the question.
    ids_in_query: list[str]
    # The ones we have NO entry for. Important: "not in our knowledge base" is NOT the same
    # as "does not exist" (for example, KEV only lists exploited CVEs). Later steps use this
    # to make CyberGPT say "I cannot confirm this ID" instead of inventing details.
    ids_not_found: list[str]


class SearchIndex:
    # k1 and b are the two standard BM25 knobs (these are the usual defaults).
    K1 = 1.5
    B = 0.75

    def __init__(self, chunks: list[Chunk]):
        self.chunks = list(chunks)

        # An "inverted index": for each word, which chunks contain it and how many times.
        # It lets us score only the chunks that contain the query words, not all of them.
        self._postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        self._lengths: list[int] = []
        self._by_ref: dict[str, list[int]] = defaultdict(list)
        # The set of words in each chunk's title (used for the title bonus in search()).
        self._title_words: list[frozenset[str]] = []

        for position, chunk in enumerate(self.chunks):
            self._title_words.append(frozenset(tokenize(chunk.title)))
            # The title is added twice, so a title match counts for more than a body match.
            words = tokenize(f"{chunk.title} {chunk.title} {chunk.ref_id} {chunk.text}")
            self._lengths.append(len(words))
            for word, count in Counter(words).items():
                self._postings[word].append((position, count))
            if chunk.ref_id:
                self._by_ref[chunk.ref_id.upper()].append(position)

        total = len(self.chunks)
        self._avg_length = (sum(self._lengths) / total) if total else 0.0

    def __len__(self) -> int:
        return len(self.chunks)

    def lookup(self, ref_id: str) -> list[Chunk]:
        """Exact lookup by official ID. Returns [] if we do not have it (we never guess)."""
        return [self.chunks[i] for i in self._by_ref.get(ref_id.strip().upper(), [])]

    def search(
        self,
        query: str,
        limit: int = 5,
        domains: list[str] | None = None,
        sources: list[str] | None = None,
    ) -> SearchResult:
        def allowed(chunk: Chunk) -> bool:
            if domains and not set(domains) & set(chunk.domains):
                return False
            if sources and chunk.source not in sources:
                return False
            return True

        hits: list[Hit] = []
        seen: set[int] = set()

        # 1) Exact IDs found in the question come first.
        ids_in_query: list[str] = []
        for found in ID_PATTERN.findall(query):
            if found.upper() not in ids_in_query:
                ids_in_query.append(found.upper())
        ids_not_found = [i for i in ids_in_query if i not in self._by_ref]

        for ref_id in ids_in_query:
            for position in self._by_ref.get(ref_id, []):
                if position not in seen and allowed(self.chunks[position]):
                    seen.add(position)
                    hits.append(Hit(self.chunks[position], score=1000.0, exact_id=True, coverage=1.0, strength=1000.0))

        # 2) Then keyword matches with BM25. The IDs are REMOVED from the keyword text first:
        #    otherwise asking about a CVE we don't have would return CVEs that merely share
        #    some digits, which looks like an answer but is not one.
        keywords = ID_PATTERN.sub(" ", query)
        total = len(self.chunks)

        # The question's topic words, each with a weight (rarer word = bigger weight).
        # A word that appears NOWHERE in our knowledge (say "log4shell") still counts, with
        # the biggest possible weight: the question is about it, and we cannot cover it.
        # Without this, a question that is mostly unknown words would look "fully covered"
        # by one lucky match on its single known word.
        top_weight = math.log(1 + (total + 0.5) / 0.5)
        weights: dict[str, float] = {}
        for word in set(tokenize(keywords)) - QUERY_FILLER:
            postings = self._postings.get(word)
            if postings:
                weights[word] = math.log(1 + (total - len(postings) + 0.5) / (len(postings) + 0.5))
            else:
                weights[word] = top_weight
        weight_sum = sum(weights.values())

        bm25: dict[int, float] = defaultdict(float)   # classic keyword score per passage
        matched: dict[int, float] = defaultdict(float)  # weight of the question words it contains
        for word, weight in weights.items():
            for position, count in self._postings.get(word, ()):
                length_factor = 1 - self.B + self.B * (self._lengths[position] / self._avg_length)
                bm25[position] += weight * (count * (self.K1 + 1)) / (count + self.K1 * length_factor)
                matched[position] += weight

        # Final score = keyword score x coverage x title bonus (see the header comment).
        scored: list[tuple[float, float, int]] = []
        weight_sum = weight_sum or 1.0  # (no topic words at all: nothing matches anyway)
        for position, raw in bm25.items():
            coverage = matched[position] / weight_sum
            in_title = sum(w for word, w in weights.items() if word in self._title_words[position])
            title_coverage = in_title / weight_sum
            final = raw * (0.4 + 0.6 * coverage) * (1 + 0.5 * title_coverage)
            scored.append((final, coverage, position))
        scored.sort(reverse=True)

        for final, coverage, position in scored:
            if len(hits) >= limit:
                break
            if position in seen or not allowed(self.chunks[position]):
                continue
            seen.add(position)
            hits.append(
                Hit(
                    self.chunks[position],
                    score=round(final, 3),
                    coverage=round(coverage, 3),
                    strength=round(final / weight_sum, 3),
                )
            )

        return SearchResult(hits=hits[:limit], ids_in_query=ids_in_query, ids_not_found=ids_not_found)