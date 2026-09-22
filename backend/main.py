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

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from access_governance.router import router as access_governance_router
from catalog_explorer.router import router as catalog_explorer_router
from dashboard.router import router as dashboard_router
from data_security.router import router as data_security_router
from job_intelligence.router import router as job_intelligence_router
from diagnostics import router as diagnostics_router
from databricks_client import set_user_token, clear_user_token


app = FastAPI(title="SentinelOps API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def capture_user_token(request: Request, call_next):
    """Extract the viewer's OAuth token from the forwarded header.
    Databricks Apps injects x-forwarded-access-token when user
    authorization is enabled on the app. This makes all downstream
    SQL/REST calls run as the logged-in user instead of the app's
    service principal."""
    token = request.headers.get("x-forwarded-access-token")
    set_user_token(token)
    try:
        response = await call_next(request)
    finally:
        clear_user_token()
    return response


app.include_router(dashboard_router)
app.include_router(catalog_explorer_router)
app.include_router(access_governance_router)
app.include_router(job_intelligence_router)
app.include_router(data_security_router)
app.include_router(diagnostics_router)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/debug/auth")
def debug_auth(request: Request):
    """Debug endpoint to verify token capture and test catalog API."""
    import base64
    import json as _json
    import requests as _requests
    from databricks_client import (
        get_user_token, _auth_headers, _sp_headers,
        rest_get_all, REST_BASE_URL,
    )

    token = get_user_token()

    # Decode JWT payload (no verification, just to see who the token is for)
    jwt_payload = None
    if token and token.count(".") == 2:
        try:
            payload_b64 = token.split(".")[1]
            payload_b64 += "=" * (4 - len(payload_b64) % 4)
            jwt_payload = _json.loads(base64.urlsafe_b64decode(payload_b64))
        except Exception as e:
            jwt_payload = {"error": str(e)}

    # Test UC REST API with user token
    user_catalogs = []
    user_error = None
    try:
        user_catalogs = rest_get_all("/api/2.1/unity-catalog/catalogs", "catalogs")
    except Exception as e:
        user_error = str(e)

    # Test UC REST API with service principal token
    sp_catalogs = []
    sp_error = None
    try:
        resp = _requests.get(
            f"{REST_BASE_URL}/api/2.1/unity-catalog/catalogs",
            headers=_sp_headers(),
            timeout=30,
        )
        if resp.status_code == 200:
            sp_catalogs = [c.get("name") for c in resp.json().get("catalogs", [])]
        else:
            sp_error = f"{resp.status_code}: {resp.text[:200]}"
    except Exception as e:
        sp_error = str(e)

    return {
        "has_user_token": token is not None,
        "token_preview": token[:20] + "..." if token else None,
        "header_present": "x-forwarded-access-token" in request.headers,
        "jwt_subject": jwt_payload.get("sub") if jwt_payload else None,
        "jwt_iss": jwt_payload.get("iss") if jwt_payload else None,
        "jwt_scope": jwt_payload.get("scope") if jwt_payload else None,
        "jwt_full": jwt_payload,
        "user_token_catalogs": [c.get("name") for c in user_catalogs],
        "user_token_error": user_error,
        "sp_catalogs": sp_catalogs,
        "sp_error": sp_error,
    }


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
