import logging
from datetime import date, datetime, timedelta, timezone

from cache import ttl_cache
from databricks_client import rest_get_all, run_query  # run_query still used by other modules

logger = logging.getLogger("sentinelops.jobs")

CACHE_SECONDS = 8
REGISTRY_CACHE_SECONDS = 300

# Databricks job run result_state values, bucketed into the three states
# this app's charts show. Anything not explicitly recognized counts as
# "failed" - a completed run is either a clean success, a deliberate
# stop, or a failure. Both the legacy Jobs API vocabulary
# (SUCCESS/FAILED/CANCELED) and the newer one (SUCCEEDED/ERROR/TIMED_OUT)
# are recognized.
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
    """One row per completed job run in the last `days` days, fetched via
    the Databricks Jobs REST API (/api/2.1/jobs/runs/list) instead of the
    system.lakeflow.job_run_timeline table. This avoids needing USE SCHEMA
    on system.lakeflow — the app's service principal only needs the
    jobs:read API scope.

    Each dict has the same keys as the old system-table version so all
    downstream callers (build_daily_trend, summarize_run_status, etc.)
    work unchanged: job_id, run_id, result_state, period_start_time,
    period_end_time, termination_code.
    """
    try:
        from_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)

        raw_runs = rest_get_all(
            "/api/2.1/jobs/runs/list",
            "runs",
            {
                "completed_only": "true",
                "run_type": "JOB_RUN",
                "start_time_from": from_ms,
                "limit": 100,
            },
            max_pages=50,
        )

        mapped: list[dict] = []
        for r in raw_runs:
            state = r.get("state") or {}
            result_state = state.get("result_state")
            if not result_state:
                continue
            start_ms = r.get("start_time")
            end_ms = r.get("end_time")
            mapped.append({
                "job_id": str(r.get("job_id")),
                "run_id": str(r.get("run_id")),
                "result_state": result_state,
                "period_start_time": datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc) if start_ms else None,
                "period_end_time": datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc) if end_ms else None,
                "termination_code": result_state,
            })
        return mapped
    except Exception:
        logger.warning("Could not fetch job runs via Jobs REST API", exc_info=True)
        return []


@ttl_cache(CACHE_SECONDS)
def fetch_running_job_count() -> int:
    """Job runs currently in progress, via the Jobs REST API."""
    try:
        runs = rest_get_all(
            "/api/2.1/jobs/runs/list",
            "runs",
            {"active_only": "true", "run_type": "JOB_RUN", "limit": 100},
            max_pages=10,
        )
        return len(runs)
    except Exception:
        logger.warning("Could not count in-progress job runs", exc_info=True)
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
        return "\u2013"
    dt = dt.astimezone()
    hour12 = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    return f"{dt.strftime('%b')} {dt.day}, {dt.year} {hour12:02d}:{dt.minute:02d} {ampm}"


def format_duration(start: datetime | None, end: datetime | None) -> str:
    if not start or not end:
        return "\u2013"
    seconds = max(0, int((end - start).total_seconds()))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def format_timestamp_ms(ms: int | None) -> str:
    return format_timestamp(datetime.fromtimestamp(ms / 1000)) if ms else "\u2013"


def format_duration_ms(start_ms: int | None, end_ms: int | None) -> str:
    if not start_ms or not end_ms:
        return "\u2013"
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
