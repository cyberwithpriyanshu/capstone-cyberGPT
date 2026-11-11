# app/knowledge/__init__.py
#
# The knowledge package: what CyberGPT knows, where it came from, and how to search it.
#   models.py   what one piece of knowledge ("chunk") looks like
#   store.py    saving and loading chunks (JSON Lines files)
#   parsers.py  turning each source's raw download into chunks
#   search.py   finding the best chunks for a question

from app.knowledge.models import DOMAINS, Chunk
from app.knowledge.search import Hit, SearchIndex, SearchResult
from app.knowledge.store import DEFAULT_DATA_DIR, load_directory

__all__ = ["Chunk", "DOMAINS", "Hit", "SearchIndex", "SearchResult", "DEFAULT_DATA_DIR", "load_directory"]