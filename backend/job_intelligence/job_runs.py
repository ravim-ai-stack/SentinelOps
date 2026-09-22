# """Job intelligence page — 'Jobs' table.

# Live — every job run in the last 30 days (system.lakeflow.job_run_timeline),
# joined to job name/tags from the Jobs REST API (job_service.fetch_job_registry).
# The UI defaults its Status filter to "Failed" so the table only shows
# failures out of the box, but any status (Success/Failed/Cancelled) can
# be picked from that filter to widen the view — so this endpoint returns
# the full set, not just failures.

# Each row is keyed by run_id — that's what "View details" passes to
# /api/jobs/{run_id}/details. RCA only applies to failed runs: the root
# cause column shows the full AI-generated summary once a user has opened
# that run's details (cached in rca_store), a quick heuristic label
# derived from the termination code until then ("Pending"), or "—" for
# runs that didn't fail.
# """

# from fastapi import APIRouter

# from job_intelligence import rca_store
# from job_service import (
#     bucket_result_state,
#     cause_label,
#     fetch_job_registry,
#     fetch_job_runs,
#     format_duration,
#     format_timestamp,
#     job_tag_label,
# )

# router = APIRouter()

# WINDOW_DAYS = 30
# MAX_ROWS = 200

# STATUS_LABELS = {"success": "Success", "failed": "Failed", "cancelled": "Cancelled"}


# @router.get("/api/jobs/runs")
# def job_runs():
#     runs = fetch_job_runs(WINDOW_DAYS)
#     registry = fetch_job_registry()

#     jobs = []
#     for r in runs[:MAX_ROWS]:
#         run_id = r["run_id"]
#         bucket = bucket_result_state(r["result_state"])
#         info = registry.get(int(r["job_id"]), {})

#         if bucket == "failed":
#             cached = rca_store.get_cached(run_id)
#             if cached:
#                 summary = cached["rca"]["root_cause"]
#                 root_cause = summary if len(summary) <= 140 else summary[:140] + "…"
#                 rca_status = "Completed"
#             else:
#                 root_cause = f"{cause_label(r.get('termination_code'))} — click View details to generate RCA"
#                 rca_status = "Pending"
#         else:
#             root_cause = "—"
#             rca_status = "—"

#         jobs.append({
#             "job_id": str(run_id),
#             "job_name": info.get("name", f"job-{r['job_id']}"),
#             "tag": job_tag_label(info.get("tags") or {}),
#             "status": STATUS_LABELS[bucket],
#             "last_run": format_timestamp(r["period_start_time"]),
#             "duration": format_duration(r["period_start_time"], r["period_end_time"]),
#             "root_cause": root_cause,
#             "rca_status": rca_status,
#         })

#     return {"jobs": jobs}


"""Job intelligence page — 'Jobs' table.

Live — every job run in the last 30 days, joined to job name/tags
from the Databricks Jobs API.

User-specific Databricks authentication is passed through the FastAPI
Request so job_service.py can use the logged-in user's
x-forwarded-access-token in a deployed Databricks App.
"""

"""Job intelligence page — 'Jobs' table.

Live — every job run in the last 30 days, joined to job name/tags
from the Databricks Jobs API.

The Databricks user's forwarded access token is passed through the
FastAPI Request so job_service.py can query Jobs using the currently
logged-in user's identity instead of the SentinelOps App Service Principal.
"""

from fastapi import APIRouter, Request

from job_intelligence import rca_store
from job_service import (
    bucket_result_state,
    cause_label,
    fetch_job_registry,
    fetch_job_runs,
    format_duration,
    format_timestamp,
    job_tag_label,
)

router = APIRouter()

WINDOW_DAYS = 30
MAX_ROWS = 200

STATUS_LABELS = {
    "success": "Success",
    "failed": "Failed",
    "cancelled": "Cancelled",
}


@router.get("/api/jobs/runs")
def job_runs(request: Request):
    # Use the currently logged-in Databricks user's identity.
    runs = fetch_job_runs(request, WINDOW_DAYS)
    registry = fetch_job_registry(request)

    jobs = []

    for r in runs[:MAX_ROWS]:
        run_id = r["run_id"]
        bucket = bucket_result_state(r["result_state"])
        info = registry.get(int(r["job_id"]), {})

        if bucket == "failed":
            cached = rca_store.get_cached(run_id)

            if cached:
                summary = cached["rca"]["root_cause"]
                root_cause = (
                    summary
                    if len(summary) <= 140
                    else summary[:140] + "…"
                )
                rca_status = "Completed"
            else:
                root_cause = (
                    f"{cause_label(r.get('termination_code'))} "
                    "— click View details to generate RCA"
                )
                rca_status = "Pending"
        else:
            root_cause = "—"
            rca_status = "—"

        jobs.append({
            "job_id": str(run_id),
            "job_name": info.get(
                "name",
                f"job-{r['job_id']}",
            ),
            "tag": job_tag_label(info.get("tags") or {}),
            "status": STATUS_LABELS[bucket],
            "last_run": format_timestamp(r["period_start_time"]),
            "duration": format_duration(
                r["period_start_time"],
                r["period_end_time"],
            ),
            "root_cause": root_cause,
            "rca_status": rca_status,
        })

    return {"jobs": jobs}