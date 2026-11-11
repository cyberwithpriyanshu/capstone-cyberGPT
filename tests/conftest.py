# tests/conftest.py
#
# PURPOSE: Shared setup that pytest runs BEFORE any test file is loaded.
#
# THE PROBLEM THIS SOLVES: our app reads its settings from your personal .env file. If that
# file contains CYBERGPT_ACCESS_PASSWORD=something (you do this to try the password gate on
# your laptop), then EVERY test that talks to the app gets "401 Unauthorized" and fails,
# even though the code is fine. Tests must give the same result on every computer, whatever
# is in anyone's .env.
#
# THE FIX: real environment variables beat the .env file, so we set the password to empty
# (meaning "no password") for the whole test run. Tests that WANT a password (test_access.py)
# pass one explicitly, and that still works.
#
# This must happen at the very top of the file, before the app is imported by test files,
# because the app reads its settings the first time it is imported.

import os

os.environ["CYBERGPT_ACCESS_PASSWORD"] = ""