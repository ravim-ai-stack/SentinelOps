"""
Job run service — real job-run history from the Databricks Jobs REST API
(/api/2.1/jobs/runs/list), used by the Dashboard's job-health panels and
the Job Intelligence page. This avoids requiring USE SCHEMA permissions on
the system.lakeflow schema for the app's service principal. Falls back to
an empty result set if the REST API call fails.

Job names/tags and per-run error detail aren't in the system tables, so
those come from the Jobs REST API (see fetch_job_registry() here and
rca_service.py for the per-run failure detail used by the Job details
drawer).
"""

import logging
from datetime import date, datetime, timedelta, timezone

from cache import ttl_cache
from databricks_client import rest_get_all, run_query

logger = logging.getLogger("sentinelops.jobs")

CACHE_SECONDS = 8
REGISTRY_CACHE_SECONDS = 300

# Databricks job run result_state values, bucketed into the three states
# this app's charts show. Anything not explicitly recognized counts as
# "failed" - a completed run is either a clean success, a deliberate
# stop, or a failure. system.lakeflow.job_run_timeline has been observed
# using both the legacy Jobs API vocabulary (SUCCESS/FAILED/CANCELED) and
# a newer one (SUCCEEDED/ERROR/TIMED_OUT) depending on workspace/runtime
# version, so both are recognized here.
SUCCESS_STATES = {"SUCCESS", "SUCCEEDED", "SUCCESS_WITH_FAILURES"}
CANCELLED_STATES = {"CANCELED", "CANCELLED", "TIMEDOUT", "TIMED_OUT", "DISABLED", "EXCLUDED", "SKIPPED"}

# Databricks Jobs API `termination_code` values, mapped to a short label
# for the "Failures by cause" chart and the failed-jobs table's root
# cause preview (before a full RCA has been generated for that run).
TERMINATION_CODE_LABELS = {
    "SUCCESS": "Success",
    "CANCELED": "Cancelled by user",
    "USER_CANCELED": "Cancelled by user",
    "SKIPPED": "Skipped",
    "INTERNAL_ERROR": "Internal platform error",
    "DRIVER_ERROR": "Driver error",
    "CLUSTER_ERROR": "Cluster provisioning error",
    "REPOSITORY_CHECKOUT_FAILED": "Repo checkout failed",
    "INVALID_CLUSTER_REQUEST": "Invalid cluster config",
    "WORKSPACE_RUN_LIMIT_EXCEEDED": "Workspace run limit exceeded",
    "FEATURE_DISABLED": "Feature disabled",
    "CLUSTER_REQUEST_LIMIT_EXCEEDED": "Cluster request limit exceeded",
    "STORAGE_ACCESS_ERROR": "Storage access error",
    "RUN_EXECUTION_ERROR": "Task execution error",
    "UNAUTHORIZED_ERROR": "Permission denied",
    "LIBRARY_INSTALLATION_ERROR": "Library install failed",
    "MAX_CONCURRENT_RUNS_EXCEEDED": "Max concurrent runs exceeded",
    "MAX_JOB_QUEUE_SIZE_EXCEEDED": "Job queue size exceeded",
    "CLOUD_FAILURE": "Cloud provider failure",
}


def bucket_result_state(result_state: str) -> str:
    if result_state in SUCCESS_STATES:
        return "success"
    if result_state in CANCELLED_STATES:
        return "cancelled"
    return "failed"


def cause_label(termination_code: str | None) -> str:
    if not termination_code:
        return "Unknown"
    return TERMINATION_CODE_LABELS.get(termination_code, termination_code.replace("_", " ").title())


@ttl_cache(CACHE_SECONDS)
def fetch_job_runs(days: int) -> list[dict]:
    """One row per completed job run in the last `days` days, including
    the owning job_id, end time and termination code (used by the Job
    Intelligence page; the Dashboard's job-health panels only read a
    subset of these columns).

    Uses the Jobs REST API (/api/2.1/jobs/runs/list) instead of
    system.lakeflow.job_run_timeline to avoid requiring USE SCHEMA
    permissions on the system.lakeflow schema for the app's service
    principal. The REST API returns the same data (job_id, run_id,
    result_state, start/end times, and termination code via
    status.termination_details.code)."""
    try:
        cutoff_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
        raw_runs = rest_get_all(
            "/api/2.1/jobs/runs/list",
            "runs",
            {"completed_only": "true", "start_time_from": cutoff_ms, "limit": 25},
            max_pages=40,
        )
        runs = []
        for r in raw_runs:
            if r.get("run_type") != "JOB_RUN":
                continue
            state = r.get("state") or {}
            result_state = state.get("result_state")
            if result_state is None:
                continue
            status = r.get("status") or {}
            term_details = status.get("termination_details") or {}
            start_ms = r.get("start_time")
            end_ms = r.get("end_time")
            runs.append({
                "job_id": r["job_id"],
                "run_id": r["run_id"],
                "result_state": result_state,
                "period_start_time": datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc) if start_ms else None,
                "period_end_time": datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc) if end_ms else None,
                "termination_code": term_details.get("code"),
            })
        runs.sort(key=lambda r: r["period_start_time"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return runs
    except Exception:
        logger.warning("Could not fetch job runs via Jobs REST API", exc_info=True)
        return []


@ttl_cache(CACHE_SECONDS)
def fetch_running_job_count() -> int:
    """Job runs currently in progress (no terminal result_state yet).

    Uses the Jobs REST API with active_only=true instead of
    system.lakeflow.job_run_timeline."""
    try:
        raw_runs = rest_get_all(
            "/api/2.1/jobs/runs/list",
            "runs",
            {"active_only": "true", "limit": 25},
            max_pages=10,
        )
        return len([r for r in raw_runs if r.get("run_type") == "JOB_RUN" and (r.get("state") or {}).get("result_state") is None])
    except Exception:
        logger.warning("Could not count in-progress job runs via Jobs REST API", exc_info=True)
        return 0


@ttl_cache(REGISTRY_CACHE_SECONDS)
def fetch_job_registry() -> dict[int, dict]:
    """job_id -> {"name", "tags"} for every job in the workspace, via the
    Jobs REST API (job name/tags aren't in the system tables)."""
    try:
        jobs = rest_get_all("/api/2.1/jobs/list", "jobs", {"limit": 100})
    except Exception:
        logger.warning("Could not list jobs via the Jobs REST API", exc_info=True)
        return {}
    registry: dict[int, dict] = {}
    for j in jobs:
        settings = j.get("settings") or {}
        registry[j["job_id"]] = {
            "name": settings.get("name") or f"job-{j['job_id']}",
            "tags": settings.get("tags") or {},
        }
    return registry


def job_tag_label(tags: dict) -> str:
    """First recognizable tag as the UI's project/team badge, falling
    back to a generic label if the job has no tags."""
    for key in ("team", "project", "department", "cost_center", "domain"):
        if tags.get(key):
            return tags[key]
    if tags:
        return next(iter(tags.values()))
    return "General"


def format_timestamp(dt: datetime | None) -> str:
    if not dt:
        return "–"
    dt = dt.astimezone()  # Jobs REST API gives UTC-aware datetimes; convert to local time for display
    hour12 = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    return f"{dt.strftime('%b')} {dt.day}, {dt.year} {hour12:02d}:{dt.minute:02d} {ampm}"


def format_duration(start: datetime | None, end: datetime | None) -> str:
    if not start or not end:
        return "–"
    seconds = max(0, int((end - start).total_seconds()))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def format_timestamp_ms(ms: int | None) -> str:
    return format_timestamp(datetime.fromtimestamp(ms / 1000)) if ms else "–"


def format_duration_ms(start_ms: int | None, end_ms: int | None) -> str:
    if not start_ms or not end_ms:
        return "–"
    return format_duration(datetime.fromtimestamp(start_ms / 1000), datetime.fromtimestamp(end_ms / 1000))


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
