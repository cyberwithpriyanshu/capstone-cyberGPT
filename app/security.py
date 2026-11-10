# app/security.py
#
# PURPOSE: An optional "front gate" for the whole website. When a password is configured,
# nobody can see the page or call the API until they enter it.
#
# WHY WE NEED IT: When CyberGPT runs only on your laptop, only you can reach it. Once it is
# hosted online, anyone who finds the link could chat with it, and every message would use
# YOUR free Groq quota (and could make the model say things under your project's name).
# A shared team password is the simplest protection, and it needs no accounts or database.
#
# HOW IT WORKS ("HTTP Basic authentication", built into every browser):
#   1. A visitor opens the site without a password.
#   2. We answer "401 Unauthorized" plus a header saying "ask for a password".
#   3. The browser shows its own small sign-in box. The visitor types ANY username and the
#      shared password. (We only check the password; the username box is just required by
#      the browser's dialog.)
#   4. The browser then re-sends the password automatically with every later request
#      (the page, the CSS, the chat calls, the streaming reply), so our JavaScript needs
#      no changes.
#
# WHY "ASGI middleware" and not a FastAPI dependency: a middleware wraps the WHOLE app, so
# it also protects static files and the /docs page, and we cannot forget to protect a new
# route later. We write it in the plain ASGI style (instead of Starlette's
# BaseHTTPMiddleware) because that style never interferes with streamed replies.

from __future__ import annotations

import base64
import binascii
import hmac

from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send


class AccessGate:
    def __init__(self, app: ASGIApp, password: str = "", open_paths: tuple[str, ...] = ("/healthz",)):
        self.app = app
        self.password = password
        # Addresses that stay open even with a password. /healthz must stay open because the
        # hosting service calls it by itself (no browser, no password) to ask "are you alive?".
        self.open_paths = open_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # No password configured, a non-web request type, or an open address: let it through.
        if (
            not self.password
            or scope["type"] != "http"
            or scope["path"] in self.open_paths
        ):
            await self.app(scope, receive, send)
            return

        if self._is_allowed(scope):
            await self.app(scope, receive, send)
            return

        # Not allowed: reply 401 and make the browser show its sign-in box.
        response = Response(
            "Access code required.",
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="CyberGPT", charset="UTF-8"'},
        )
        await response(scope, receive, send)

    def _is_allowed(self, scope: Scope) -> bool:
        # Header names arrive as lowercase bytes, e.g. (b"authorization", b"Basic dXNlcjpwdw==")
        headers = dict(scope["headers"])
        value = headers.get(b"authorization", b"").decode("latin-1")

        scheme, _, encoded = value.partition(" ")
        if scheme.lower() != "basic" or not encoded:
            return False

        try:
            decoded = base64.b64decode(encoded.strip(), validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            return False  # garbage in the header: just say no, never crash

        # The decoded text looks like "username:password". Keep only what follows the FIRST colon.
        _, sep, supplied = decoded.partition(":")
        if not sep:
            return False

        # compare_digest takes the same time however many characters match, so an attacker
        # cannot guess the password one letter at a time by measuring response speed.
        return hmac.compare_digest(supplied.encode("utf-8"), self.password.encode("utf-8"))