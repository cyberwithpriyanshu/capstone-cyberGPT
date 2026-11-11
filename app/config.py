# app/config.py
#
# PURPOSE: One central place for all settings (which AI model to use, where it runs, etc.).
#
# WHY: If we typed the model name directly inside many files, changing the model later
# would mean editing all of them. Instead, every file asks THIS file for settings, and the
# real values come from the .env file. To change the model, you only edit .env.

from functools import lru_cache
from typing import Literal

# BaseSettings is a ready-made class from the pydantic-settings library. It automatically
# reads values from environment variables and from the .env file.
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Tell BaseSettings how to behave:
    #  - env_file:   read the file named ".env" (in the folder you run the app from)
    #  - env_prefix: only look at variables that start with CYBERGPT_
    #                (so CYBERGPT_LLM_MODEL in .env fills the field `llm_model` below)
    #  - extra="ignore": don't crash if .env has variables we don't know about
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="CYBERGPT_",
        extra="ignore",
    )

    # Basic info about the app. The text after ":" is the type (str = text),
    # and the value after "=" is the default used if .env doesn't set it.
    app_name: str = "CyberGPT"
    app_version: str = "0.1.0"

    # Which backend to use. Literal[...] means ONLY these exact words are allowed, so a
    # typo like "fakee" gives a clear error at startup instead of a confusing bug later.
    llm_provider: Literal["openai_compat", "fake"] = "openai_compat"

    # Where the AI model server lives. Ollama runs on your own computer at port 11434.
    llm_base_url: str = "http://localhost:11434/v1"

    # Name of the model to use (must be installed in Ollama).
    llm_model: str = "llama3.2:3b"

    # Ollama ignores the key, but the standard request format expects one.
    llm_api_key: str = "ollama"

    # Seconds to wait for the model before giving up.
    llm_timeout_seconds: float = 120.0

    # Creativity dial. Low = consistent and factual, which suits security work.
    llm_temperature: float = 0.2

    # Maximum length of a reply (in tokens; one token is roughly 3/4 of a word).
    llm_max_tokens: int = 1024

    
    # NEW in Step 2.5: an optional shared password for the whole site.
    # WHY: once CyberGPT is online, its address is public, and every message anyone sends
    # is paid for with YOUR Groq quota. If this is empty (the default, fine on your own
    # laptop) there is no password. If you set it (do this on the hosted copy), the browser
    # asks for it before showing anything.
    access_password: str = ""

    
    # NEW in Step 4: the knowledge base is now used when answering.
    #  - rag_enabled:     look up passages in the knowledge base before the AI answers.
    #                     Set to false to get plain chat back (handy to compare answers).
    #  - rag_rewrite:     first ask the AI for better search keywords ("log4shell" -> "Apache
    #                     Log4j2 remote code execution"). It costs one extra small AI call
    #                     per message; turn it off if you hit your free-quota limit.
    #  - rag_max_sources: the most passages we hand to the AI (1 to 8). More passages give
    #                     more material but make every answer slower and less focused.
    rag_enabled: bool = True
    rag_rewrite: bool = True
    rag_max_sources: int = 4


# lru_cache means "remember the result". The first call creates the Settings object
# (and reads .env). Every later call reuses it instead of re-reading the file.
@lru_cache
def get_settings() -> Settings:
    return Settings()