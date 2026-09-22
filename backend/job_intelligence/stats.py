# """Job intelligence page — the 5 stat cards.

# Live — run counts come from system.lakeflow.job_run_timeline (see
# job_service.py); "RCA generated" counts how many failed runs have had a
# root cause analysis generated so far via the Job details drawer (see
# rca_store.py) — it's a per-session count, not a lifetime one, since RCAs
# aren't persisted.
# """

# from fastapi import APIRouter

# from job_intelligence import rca_store
# from job_service import fetch_job_runs, fetch_running_job_count, summarize_run_status

# router = APIRouter()

# WINDOW_DAYS = 30


# @router.get("/api/jobs/stats")
# def jobs_stats():
#     summary = summarize_run_status(fetch_job_runs(WINDOW_DAYS))
#     return {
#         "total_runs": summary["total_runs"],
#         "successful_runs": summary["success"],
#         "failed_runs": summary["failed"],
#         "running_jobs": fetch_running_job_count(),
#         "rca_generated": rca_store.count(),
#     }

"""Job intelligence page — the 5 stat cards."""

from fastapi import APIRouter, Request

from job_intelligence import rca_store
from job_service import (
    fetch_job_runs,
    fetch_running_job_count,
    summarize_run_status,
)

router = APIRouter()

WINDOW_DAYS = 30


@router.get("/api/jobs/stats")
def jobs_stats(request: Request):
    summary = summarize_run_status(
        fetch_job_runs(request, WINDOW_DAYS)
    )

    return {
        "total_runs": summary["total_runs"],
        "successful_runs": summary["success"],
        "failed_runs": summary["failed"],
        "running_jobs": fetch_running_job_count(request),
        "rca_generated": rca_store.count(),
    }