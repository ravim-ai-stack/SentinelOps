"""
diagnostics.py — TEMPORARY. Delete once the jobs page is working.

Reports what the running app can actually see: which forwarded headers
arrive, whether a viewer token is present, and which system schemas each
identity can read. This answers in one request what would otherwise take
several deploy cycles to infer from tracebacks.

Wire it up in main.py:

    from diagnostics import router as diagnostics_router
    app.include_router(diagnostics_router)

Then open  /api/diag  in the browser while logged into the app.

SAFETY: reports only token PRESENCE and LENGTH, never the token itself,
and truncates error text. Still, delete this file when you're done - it
exposes internal configuration to anyone who can open the app.
"""

import os
import traceback

from fastapi import APIRouter, Request

from databricks_client import DATABRICKS_WAREHOUSE_ID, run_query
from viewer_sql import run_query_as_viewer, viewer_id, viewer_token

router = APIRouter()

_PROBES = {
    "system.lakeflow.job_run_timeline": (
        "SELECT count(*) AS n FROM system.lakeflow.job_run_timeline "
        "WHERE period_start_time >= current_timestamp() - INTERVAL 7 DAYS"
    ),
    "system.lakeflow.jobs": "SELECT count(*) AS n FROM system.lakeflow.jobs",
    "system.lakeflow.job_task_run_timeline": (
        "SELECT count(*) AS n FROM system.lakeflow.job_task_run_timeline "
        "WHERE period_start_time >= current_timestamp() - INTERVAL 7 DAYS"
    ),
    "system.access.table_lineage": (
        "SELECT count(*) AS n FROM system.access.table_lineage "
        "WHERE event_date >= current_date() - INTERVAL 7 DAYS"
    ),
}


def _probe(runner) -> dict:
    out = {}
    for label, sql in _PROBES.items():
        try:
            rows = runner(sql)
            out[label] = {"ok": True, "rows": rows[0].get("n") if rows else 0}
        except Exception as exc:
            out[label] = {
                "ok": False,
                "error": f"{type(exc).__name__}: {str(exc)[:300]}",
            }
    return out


@router.get("/api/diag")
def diag(request: Request):
    token = viewer_token(request)

    forwarded = {
        name: (
            f"<present, {len(value)} chars>"
            if "token" in name.lower()
            else value
        )
        for name, value in request.headers.items()
        if name.lower().startswith("x-forwarded")
    }

    report = {
        "viewer": {
            "identity": viewer_id(request),
            "token_present": bool(token),
            "token_length": len(token) if token else 0,
            "forwarded_headers": forwarded or "NONE - auth proxy sent nothing",
        },
        "env": {
            "warehouse_id": DATABRICKS_WAREHOUSE_ID,
            "workspace_id": os.getenv("DATABRICKS_WORKSPACE_ID") or "NOT SET",
            "app_name": os.getenv("DATABRICKS_APP_NAME") or "NOT SET",
            "client_id": os.getenv("DATABRICKS_CLIENT_ID") or "NOT SET",
        },
    }

    if token:
        report["as_viewer"] = _probe(
            lambda sql: run_query_as_viewer(token, sql)
        )
    else:
        report["as_viewer"] = "SKIPPED - no x-forwarded-access-token"

    report["as_service_principal"] = _probe(lambda sql: run_query(sql))

    # Exercise the real code path too, so we see what the panels see.
    try:
        from job_service import fetch_job_runs

        runs = fetch_job_runs(request, 30)
        report["fetch_job_runs"] = {
            "rows": len(runs),
            "sample": runs[0] if runs else None,
        }
    except Exception:
        report["fetch_job_runs"] = {
            "error": traceback.format_exc()[-1500:]
        }

    return report