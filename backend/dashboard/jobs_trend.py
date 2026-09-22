# """Dashboard page — 'Jobs Trend' line chart.

# Live — one point per day for the last 14 days, from
# system.lakeflow.job_run_timeline (falls back to all-zero days if that
# system table isn't enabled on this workspace's metastore).
# """

# from fastapi import APIRouter

# from job_service import build_daily_trend, fetch_job_runs

# router = APIRouter()

# WINDOW_DAYS = 14


# @router.get("/api/dashboard/jobs-trend")
# def jobs_trend():
#     return {"points": build_daily_trend(fetch_job_runs(WINDOW_DAYS), WINDOW_DAYS)}

"""Dashboard page — 'Jobs Trend' line chart.

Live — one point per day for the last 14 days, from
system.lakeflow.job_run_timeline (falls back to all-zero days if that
system table isn't enabled on this workspace's metastore).
"""

from fastapi import APIRouter, Request

from job_service import build_daily_trend, fetch_job_runs

router = APIRouter()

WINDOW_DAYS = 14


@router.get("/api/dashboard/jobs-trend")
def jobs_trend(request: Request):
    return {
        "points": build_daily_trend(
            fetch_job_runs(request, WINDOW_DAYS),
            WINDOW_DAYS,
        )
    }