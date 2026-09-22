# """Job intelligence page — 'Job runs trend' line chart.

# Live — same job-run query as the Dashboard's Jobs Trend panel
# (system.lakeflow.job_run_timeline), one point per day for the last 14 days.
# """

# from fastapi import APIRouter

# from job_service import build_daily_trend, fetch_job_runs

# router = APIRouter()

# WINDOW_DAYS = 14


# @router.get("/api/jobs/runs-trend")
# def runs_trend():
#     return {"points": build_daily_trend(fetch_job_runs(WINDOW_DAYS), WINDOW_DAYS)}


"""Job intelligence page — 'Job runs trend' line chart.

Live — same job-run query as the Dashboard's Jobs Trend panel
(system.lakeflow.job_run_timeline), one point per day for the last 14 days.
"""

from fastapi import APIRouter, Request

from job_service import build_daily_trend, fetch_job_runs

router = APIRouter()

WINDOW_DAYS = 14


@router.get("/api/jobs/runs-trend")
def runs_trend(request: Request):
    return {
        "points": build_daily_trend(
            fetch_job_runs(request, WINDOW_DAYS),
            WINDOW_DAYS,
        )
    }