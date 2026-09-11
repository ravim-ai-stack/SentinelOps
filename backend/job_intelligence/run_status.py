"""Job intelligence page — 'Job run status' donut.

Live — same job-run query as the Dashboard's Job Health Overview
(system.lakeflow.job_run_timeline, last 30 days).
"""

from fastapi import APIRouter

from job_service import fetch_job_runs, summarize_run_status

router = APIRouter()

WINDOW_DAYS = 30


@router.get("/api/jobs/run-status")
def run_status():
    return summarize_run_status(fetch_job_runs(WINDOW_DAYS))
