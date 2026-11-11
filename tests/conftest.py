# tests/conftest.py
#
# PURPOSE: Settings that apply to EVERY test, applied before any test file is imported.
# pytest loads this file automatically.
#
# WHY: your real `.env` file holds real settings (a Groq key, an access password...). Tests
# must never depend on them, and must never talk to the real AI. Environment variables beat
# the `.env` file, so setting them here makes every test run in the same safe, predictable
# situation, whatever is in your `.env`.

import os

# No password gate, so tests do not get "401 Unauthorized" because YOUR .env has a password.
os.environ["CYBERGPT_ACCESS_PASSWORD"] = ""

# Never contact a real AI service from a test (it would be slow, cost quota and fail offline).
os.environ["CYBERGPT_LLM_PROVIDER"] = "fake"

# Step 4: do not make the extra "suggest search keywords" AI call in tests unless a test asks
# for it on purpose (the rewrite tests pass their own settings).
os.environ["CYBERGPT_RAG_REWRITE"] = "false"