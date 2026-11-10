# app/llm/__init__.py
#
# This file does two jobs:
#  1. Marks the folder as a Python package (see app/__init__.py).
#  2. Re-exports the most-used names so other files can write the short form
#         from app.llm import ChatMessage, build_provider
#     instead of the long form
#         from app.llm.base import ChatMessage
#         from app.llm.factory import build_provider

from app.llm.base import ChatMessage, LLMError, LLMProvider, LLMResponse, ProviderHealth
from app.llm.factory import build_provider

# __all__ lists the names that are "public" from this package.
__all__ = [
    "ChatMessage",
    "LLMError",
    "LLMProvider",
    "LLMResponse",
    "ProviderHealth",
    "build_provider",
]