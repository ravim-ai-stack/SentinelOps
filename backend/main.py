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

Run:
    pip install -r requirements.txt
    copy .env.example .env      # fill in DATABRICKS_HOST / TOKEN / WAREHOUSE_ID
    python main.py               # starts the API and opens frontend/ui.html
    # or, for auto-reload during development:
    uvicorn main:app --reload --port 3001
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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

app.include_router(dashboard_router)
app.include_router(catalog_explorer_router)
app.include_router(access_governance_router)
app.include_router(job_intelligence_router)
app.include_router(data_security_router)


@app.get("/api/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import threading
    import webbrowser
    from pathlib import Path

    import uvicorn

    HOST, PORT = "127.0.0.1", 3001
    ui_path = Path(__file__).resolve().parent.parent / "frontend" / "ui.html"

    def _open_ui():
        webbrowser.open(ui_path.as_uri())

    threading.Timer(1.5, _open_ui).start()
    uvicorn.run(app, host=HOST, port=PORT)
