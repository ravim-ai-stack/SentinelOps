"""Dashboard page — 'Job Health Overview' donut.

Live — reads job run history from system.lakeflow.job_run_timeline
(falls back to zeros if that system table isn't enabled on this
workspace's metastore).
"""

from fastapi import APIRouter

from job_service import fetch_job_runs, summarize_run_status

router = APIRouter()

WINDOW_DAYS = 30


@router.get("/api/dashboard/job-health-overview")
def job_health_overview():
    return summarize_run_status(fetch_job_runs(WINDOW_DAYS))
