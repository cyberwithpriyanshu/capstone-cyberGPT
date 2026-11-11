# app/rag/__init__.py
#
# "RAG" = Retrieval-Augmented Generation: before the AI answers, we RETRIEVE the passages
# from our knowledge base that relate to the question, and hand them to the AI together with
# the question, so the answer is grounded in real sources that we can cite.
#
# Files in this folder:
#   rewrite.py    asks the AI for better search keywords (fixes "log4shell"-style misses)
#   retrieval.py  runs the search, drops weak matches, returns what to use
#   context.py    turns the passages into the text block the AI reads, and into the
#                 "source cards" the web page shows

from app.rag.context import SourceCard, build_sources_block, source_cards
from app.rag.retrieval import Retrieval, retrieve

__all__ = ["Retrieval", "retrieve", "SourceCard", "build_sources_block", "source_cards"]