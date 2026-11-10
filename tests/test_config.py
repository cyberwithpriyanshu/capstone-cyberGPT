# tests/test_config.py
#
# PURPOSE: Check that our settings (app/config.py) behave the way we promised.
#
# HOW TESTS WORK (the 30-second version):
#  - pytest finds every file named test_*.py and runs every function named test_*.
#  - Inside a test we write `assert <something that should be true>`.
#    If it is false, the test FAILS and pytest shows exactly what was different.
#  - A passing test means "this behavior still works". We re-run all tests after every
#    change, so if we accidentally break something we find out immediately.

import pytest
from pydantic import ValidationError

from app.config import Settings


# `monkeypatch` is a helper that pytest hands us automatically (just by listing it as a
# parameter). It lets a test change environment variables TEMPORARILY; pytest undoes
# every change when the test ends, so tests can't affect each other or your real setup.
def test_defaults_point_at_local_ollama(monkeypatch):
    # Remove any CYBERGPT_ variables on your computer so we test the true defaults.
    for key in ("PROVIDER", "BASE_URL", "MODEL", "API_KEY"):
        monkeypatch.delenv(f"CYBERGPT_LLM_{key}", raising=False)

    # _env_file=None means "do NOT read the .env file", so this test gives the same
    # result no matter what you personally have written in your .env.
    s = Settings(_env_file=None)

    assert s.llm_provider == "openai_compat"
    assert s.llm_base_url == "http://localhost:11434/v1"
    assert s.llm_model == "llama3.2:3b"
    assert s.llm_timeout_seconds == 120.0


def test_environment_variables_override_defaults(monkeypatch):
    # Pretend the user set these variables, then check Settings picked them up.
    monkeypatch.setenv("CYBERGPT_LLM_MODEL", "qwen2.5:3b")
    monkeypatch.setenv("CYBERGPT_LLM_BASE_URL", "http://example.test/v1")
    monkeypatch.setenv("CYBERGPT_LLM_TEMPERATURE", "0.7")

    s = Settings(_env_file=None)

    assert s.llm_model == "qwen2.5:3b"
    assert s.llm_base_url == "http://example.test/v1"
    # The text "0.7" from the environment was converted to a real number automatically.
    assert s.llm_temperature == 0.7


def test_invalid_provider_is_rejected(monkeypatch):
    monkeypatch.setenv("CYBERGPT_LLM_PROVIDER", "does-not-exist")

    # `pytest.raises(...)` says: "I EXPECT this code to fail with this kind of error".
    # The test passes only if the error really happens. Here we confirm that a typo in
    # the provider name is caught immediately instead of causing a mysterious bug later.
    with pytest.raises(ValidationError):
        Settings(_env_file=None)