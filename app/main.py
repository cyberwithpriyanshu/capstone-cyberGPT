# app/main.py
#
# PURPOSE: The front door of the application. This is the file the web server starts.
# It creates the FastAPI app, plugs in our routes, and (NEW in Step 2B) serves the web page.
#
# Start the app (from the project folder) with:
#     uvicorn app.main:app --reload
#   - "app.main"  = the file app/main.py
#   - ":app"      = the variable named `app` at the bottom of this file
#   - --reload    = restart automatically whenever you save a change (great while developing)
#
# Then open:
#   http://127.0.0.1:8000        the CyberGPT web page
#   http://127.0.0.1:8000/docs   the automatic API documentation page

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import Settings, get_settings
from app.security import AccessGate

# The folder holding the web page files (index.html, style.css, app.js, markdown.js).
# __file__ is this file's own location, so this works no matter where you run the app from.
STATIC_DIR = Path(__file__).parent / "static"


class NoCacheStaticFiles(StaticFiles):
    """Serves the web page files, but tells the browser to re-check them every time.

    WHY: while you are editing style.css or app.js, you want a normal refresh to show your
    latest changes, not an old copy the browser saved. ("no-cache" does NOT mean "don't
    store"; it means "ask the server whether the file changed before using your copy", which
    is fast.)
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


def create_app(settings: Settings | None = None) -> FastAPI:
    # Normally we read the real settings; tests pass in their own to try different setups.
    settings = settings or get_settings()

    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Evidence-grounded cybersecurity assistant (capstone project).",
    )

    # Plug in the routes we defined in app/api/routes.py (/health, /modes, /chat, /chat/stream).
    application.include_router(router)

    # Everything inside app/static is available under the address /static/...
    # For example app/static/style.css is served at /static/style.css.
    application.mount("/static", NoCacheStaticFiles(directory=STATIC_DIR), name="static")

    # A tiny "are you alive?" address for the hosting service. It deliberately does NOT
    # contact the AI model (unlike /health), so it is instant and reveals nothing.
    @application.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    # The home address "/" shows the chat page.
    # include_in_schema=False hides it from the /docs page to keep that page clean.
    @application.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    # NEW in Step 2.5: the optional password gate. It is added LAST so it wraps everything
    # above it (pages, static files, API, /docs). With no password set it does nothing.
    application.add_middleware(AccessGate, password=settings.access_password)

    return application


# The web server looks for exactly this variable.
app = create_app()