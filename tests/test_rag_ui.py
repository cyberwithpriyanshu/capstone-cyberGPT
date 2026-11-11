# tests/test_rag_ui.py
#
# PURPOSE: Light checks that the Step 4 page code is in place and safe. (How it LOOKS and
# behaves in a browser you check with your eyes; these catch a missing or broken edit.)

from fastapi.testclient import TestClient

from app.main import app


def get(path):
    with TestClient(app) as c:
        return c.get(path).text


def test_script_understands_the_sources_message_and_the_plain_reply():
    js = get("/static/app.js")
    assert 'event.type === "sources"' in js        # streaming route
    assert "data.knowledge" in js                  # non-streaming fallback (used on Render)
    assert "linkCitations" in js and "addSources" in js


def test_source_text_is_never_put_in_the_page_as_html():
    js = get("/static/app.js")
    start = js.index("// Sources and citations")
    end = js.index("// Copy buttons inside code blocks")
    block = js[start:end]
    assert "innerHTML" not in block                # titles come from outside documents
    assert "safeUrl" in block and "noopener" in block  # only http(s) links, safely opened


def test_stylesheet_has_the_source_styles():
    css = get("/static/style.css")
    for selector in (".cite", ".sources", ".source-warn", ".source-n"):
        assert selector in css