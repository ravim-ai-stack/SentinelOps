"""
SentinelOps backend - orchestrator

The backend is organized one folder per UI page (dashboard/,
catalog_explorer/, access_governance/, job_intelligence/, data_security/),
and one file per panel/tab within that page - each panel file defines its
own FastAPI router with its own endpoint. Every page folder has a
router.py that combines its panel routers into one; this file just wires
the five page routers into the app.

Shared query/computation logic (used by more than one panel) lives in the
top-level *_service.py modules; databricks_client.py is the connection
layer to Databricks itself.

Run locally:
    pip install -r requirements.txt
    copy .env.example .env      # fill in DATABRICKS_HOST / TOKEN / WAREHOUSE_ID
    python main.py               # starts the API and opens the frontend at http://127.0.0.1:3001/
    # or, for auto-reload during development:
    uvicorn main:app --reload --port 3001

Deployed as a Databricks App (see app.yaml): no .env needed, no login
page to build - the platform handles auth (both the app's own
credentials and the user's browser login) before any request reaches
this file. See databricks_client.py for details.

The frontend is served by this same app (mounted below) so there is a
single URL both locally and once deployed - the browser never talks to
a different host/port, which is required for a Databricks App (only
one URL is exposed) and is why frontend/api.js uses relative paths.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import request_context
from access_governance.router import router as access_governance_router
from catalog_explorer.router import router as catalog_explorer_router
from dashboard.router import router as dashboard_router
from data_security.router import router as data_security_router
from job_intelligence.router import router as job_intelligence_router

app = FastAPI(title="SentinelOps API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ViewerIdentityMiddleware:
    """Databricks Apps forwards the logged-in viewer's own OAuth access
    token on X-Forwarded-Access-Token once User Authorization is enabled on
    the app deployment (see app.yaml). Stash it in request_context for the
    duration of this request so databricks_client.py can authenticate as
    the viewer instead of the app's service principal, and cache.py can key
    cached results per-viewer. Absent locally / if User Authorization isn't
    enabled - both context vars stay None and databricks_client.py falls
    back to the service-principal identity exactly as before.

    A plain ASGI middleware (rather than @app.middleware("http") /
    BaseHTTPMiddleware) on purpose: BaseHTTPMiddleware runs the downstream
    app in a separate task wired up through an in-memory stream, which does
    not reliably propagate contextvars set beforehand. This class instead
    stays in the same coroutine/task all the way into the route handler
    (and from there into anyio's thread pool, which does propagate
    contextvars), so the values set here are guaranteed visible deep in a
    panel's call stack."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        headers = dict(scope["headers"])  # raw ASGI headers: bytes keys/values
        token = headers.get(b"x-forwarded-access-token")
        email = headers.get(b"x-forwarded-email")

        token_reset = request_context.user_token.set(token.decode() if token else None)
        email_reset = request_context.user_email.set(email.decode() if email else None)
        try:
            await self.app(scope, receive, send)
        finally:
            request_context.user_token.reset(token_reset)
            request_context.user_email.reset(email_reset)


app.add_middleware(ViewerIdentityMiddleware)

app.include_router(dashboard_router)
app.include_router(catalog_explorer_router)
app.include_router(access_governance_router)
app.include_router(job_intelligence_router)
app.include_router(data_security_router)


@app.get("/api/health")
def health():
    return {"status": "ok"}


# Mounted last so it never shadows the /api/* routes above - it only
# catches whatever those routers didn't already handle (the frontend's
# index.html, styles.css, app.js, api.js).
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")


if __name__ == "__main__":
    import os

    import uvicorn

    # Databricks Apps sets DATABRICKS_APP_PORT and expects the app to bind
    # 0.0.0.0; locally there's no such env var, so fall back to the old
    # dev behavior of opening the static frontend in a browser.
    app_port = os.environ.get("DATABRICKS_APP_PORT")

    if app_port:
        uvicorn.run(app, host="0.0.0.0", port=int(app_port))
    else:
        import threading
        import webbrowser

        HOST, PORT = "127.0.0.1", 3001

        def _open_ui():
            webbrowser.open(f"http://{HOST}:{PORT}/")

        threading.Timer(1.5, _open_ui).start()
        uvicorn.run(app, host=HOST, port=PORT)
