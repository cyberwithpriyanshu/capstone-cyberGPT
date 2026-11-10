# tests/test_access.py
#
# PURPOSE: Check the optional password gate (app/security.py) and the /healthz address.
#
# We build a fresh app for each test with create_app(Settings(...)), so we can try
# "no password" and "with password" without touching your real .env file.

import base64

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

PASSWORD = "team-secret-123"


def basic(username: str, password: str) -> dict:
    """Builds the header a browser sends after you fill in its sign-in box."""
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def make_client(password: str) -> TestClient:
    # llm_provider="fake" so no real AI is ever contacted by these tests.
    settings = Settings(llm_provider="fake", access_password=password)
    return TestClient(create_app(settings))


@pytest.fixture
def open_client():
    """A site with NO password (how it behaves on your laptop)."""
    with make_client("") as c:
        yield c


@pytest.fixture
def locked_client():
    """A site WITH a password (how the hosted copy behaves)."""
    with make_client(PASSWORD) as c:
        yield c


# ---------- no password configured ----------

def test_without_a_password_everything_is_open(open_client):
    assert open_client.get("/").status_code == 200
    assert open_client.get("/modes").status_code == 200


# ---------- password configured ----------

def test_page_is_refused_without_a_password(locked_client):
    r = locked_client.get("/")

    assert r.status_code == 401
    # This header is what makes the browser show its sign-in box.
    assert r.headers["www-authenticate"].startswith("Basic")


def test_correct_password_is_accepted(locked_client):
    r = locked_client.get("/", headers=basic("anyone", PASSWORD))

    assert r.status_code == 200
    assert "<title>CyberGPT</title>" in r.text


def test_username_does_not_matter_only_the_password(locked_client):
    for name in ("team", "", "priyanshu"):
        assert locked_client.get("/modes", headers=basic(name, PASSWORD)).status_code == 200


def test_wrong_password_is_refused(locked_client):
    for wrong in ("nope", PASSWORD + "x", PASSWORD[:-1], ""):
        assert locked_client.get("/", headers=basic("u", wrong)).status_code == 401


def test_password_containing_a_colon_works(locked_client):
    # Basic auth joins "user:password"; a colon INSIDE the password must still work.
    with make_client("pa:ss:word") as c:
        assert c.get("/", headers=basic("u", "pa:ss:word")).status_code == 200


def test_non_ascii_password_works():
    with make_client("pässwörd-✓") as c:
        assert c.get("/", headers=basic("u", "pässwörd-✓")).status_code == 200
        assert c.get("/", headers=basic("u", "password")).status_code == 401


@pytest.mark.parametrize(
    "header",
    [
        "Basic",                      # no value
        "Basic !!!not-base64!!!",     # not base64 at all
        "Bearer abc123",              # a different scheme
        "Basic " + base64.b64encode(b"no-colon-here").decode(),  # missing the colon
        "Basic " + base64.b64encode(b"\xff\xfe:\xff").decode(),  # not valid UTF-8
    ],
)
def test_malformed_headers_are_refused_not_crashed(locked_client, header):
    r = locked_client.get("/", headers={"Authorization": header})
    assert r.status_code == 401


def test_every_part_of_the_site_is_protected(locked_client):
    # The gate wraps the whole app, so nothing was forgotten.
    assert locked_client.get("/static/app.js").status_code == 401
    assert locked_client.get("/docs").status_code == 401
    assert locked_client.get("/health").status_code == 401
    assert locked_client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]}).status_code == 401
    assert locked_client.post("/chat/stream", json={"messages": [{"role": "user", "content": "hi"}]}).status_code == 401


def test_streaming_chat_works_once_signed_in(locked_client):
    r = locked_client.post(
        "/chat/stream",
        json={"messages": [{"role": "user", "content": "hello"}], "mode": "learn"},
        headers=basic("u", PASSWORD),
    )

    assert r.status_code == 200
    assert '"type": "done"' in r.text or '"type":"done"' in r.text


# ---------- /healthz ----------

def test_healthz_is_always_open_and_tiny(locked_client, open_client):
    # The hosting service calls this without a password, so it must stay open.
    for c in (locked_client, open_client):
        r = c.get("/healthz")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}