# tests/test_ui.py
#
# PURPOSE: Check that the web page and its files are served correctly. These tests cannot
# judge how the page LOOKS (you do that with your eyes), but they catch the most common
# problems: a missing file, a wrong file name, or a page that points to files that don't exist.

import re

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_home_page_is_served_as_html(client):
    r = client.get("/")

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "<title>CyberGPT</title>" in r.text


def test_every_file_the_page_loads_from_static_exists(client):
    html = client.get("/").text

    # Find every address like /static/style.css mentioned in the page.
    paths = set(re.findall(r'(?:href|src)="(/static/[^"]+)"', html))

    # The page must use all three of our files...
    assert {"/static/style.css", "/static/markdown.js", "/static/app.js"} <= paths
    # ...and every one must actually be served.
    for path in paths:
        assert client.get(path).status_code == 200, f"{path} is missing"


def test_files_are_served_with_the_right_content_types(client):
    assert "text/css" in client.get("/static/style.css").headers["content-type"]
    assert "javascript" in client.get("/static/app.js").headers["content-type"]
    assert "javascript" in client.get("/static/markdown.js").headers["content-type"]


def test_ui_files_must_be_rechecked_by_the_browser(client):
    # So a normal refresh shows your latest edits (see NoCacheStaticFiles in main.py).
    for path in ("/", "/static/style.css", "/static/app.js"):
        assert client.get(path).headers["cache-control"] == "no-cache"


def test_page_contains_the_elements_app_js_depends_on(client):
    html = client.get("/").text

    # app.js looks these up by id; if one is renamed in the HTML the page breaks silently.
    for element_id in (
        "app", "thread", "threadInner", "composer", "input", "send", "modes",
        "modeHint", "suggestions", "status", "statusText", "hint", "newChat",
        "themes", "announce",
    ):
        assert f'id="{element_id}"' in html, f"index.html lost the element id '{element_id}'"


def test_unknown_static_file_is_a_404(client):
    assert client.get("/static/nope.js").status_code == 404


def test_api_docs_still_work(client):
    assert client.get("/docs").status_code == 200