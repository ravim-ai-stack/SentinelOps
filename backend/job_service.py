"""
Job run service — real job-run history from Databricks system tables
(system.lakeflow.job_run_timeline), used by the Dashboard's job-health
panels. Falls back to an empty result set if that system table isn't
enabled on this workspace's metastore, the same way catalog_service
falls back for volumes/models.
"""

import logging
from datetime import date, timedelta

from cache import ttl_cache
from databricks_client import run_query

logger = logging.getLogger("sentinelops.jobs")

CACHE_SECONDS = 8

# Databricks job run result_state values, bucketed into the three states
# this app's charts show. Anything not explicitly recognized counts as
# "failed" - a completed run is either a clean success, a deliberate
# stop, or a failure.
SUCCESS_STATES = {"SUCCESS", "SUCCESS_WITH_FAILURES"}
CANCELLED_STATES = {"CANCELED", "CANCELLED", "TIMEDOUT", "DISABLED", "EXCLUDED"}


def bucket_result_state(result_state: str) -> str:
    if result_state in SUCCESS_STATES:
        return "success"
    if result_state in CANCELLED_STATES:
        return "cancelled"
    return "failed"


@ttl_cache(CACHE_SECONDS)
def fetch_job_runs(days: int) -> list[dict]:
    """One row per completed job run in the last `days` days."""
    try:
        return run_query(
            f"""
            SELECT run_id, result_state, period_start_time, termination_code
            FROM system.lakeflow.job_run_timeline
            WHERE run_type = 'JOB_RUN'
              AND period_start_time >= current_timestamp() - INTERVAL {int(days)} DAYS
              AND result_state IS NOT NULL
            """
        )
    except Exception:
        logger.warning("system.lakeflow.job_run_timeline not available on this workspace", exc_info=True)
        return []


def summarize_run_status(runs: list[dict]) -> dict:
    counts = {"success": 0, "failed": 0, "cancelled": 0}
    for r in runs:
        counts[bucket_result_state(r["result_state"])] += 1
    return {"total_runs": len(runs), **counts}


def build_daily_trend(runs: list[dict], days: int) -> list[dict]:
    """One point per day for the last `days` days (oldest first), each
    with success/failed/cancelled counts for that day."""
    today = date.today()
    buckets: dict[date, dict] = {
        today - timedelta(days=offset): {"success": 0, "failed": 0, "cancelled": 0}
        for offset in range(days - 1, -1, -1)
    }

    for r in runs:
        run_date = r["period_start_time"].date()
        bucket = buckets.get(run_date)
        if bucket is not None:
            bucket[bucket_result_state(r["result_state"])] += 1

    return [
        {"label": f"{d.strftime('%b')} {d.day}", **counts}
        for d, counts in buckets.items()
    ]
