"""
job_service.py  —  Job runs & registry via the Databricks SDK
================================================================

Replaces the original system.lakeflow SQL queries with SDK calls
(w.jobs.list / w.jobs.list_runs).  The WorkspaceClient auto-authenticates
as the app's service principal inside the Databricks Apps environment.

Requirements:
  • 'databricks-sdk' must be in the app's requirements.txt
  • The app's service principal must have CAN_VIEW on each job it should
    see.  A workspace admin can grant this via:

      w.permissions.update(
          request_object_type="jobs",
          request_object_id="<job_id>",
          access_control_list=[AccessControlRequest(
              service_principal_name="app-qx8gqw sentinelopspod",
              permission_level=PermissionLevel.CAN_VIEW,
          )]
      )

No system.lakeflow GRANT or user-authorization scopes are needed.
"""

import logging
from datetime import date, datetime, timedelta, timezone

from cache import ttl_cache
from databricks.sdk import WorkspaceClient

logger = logging.getLogger("sentinelops.jobs")

CACHE_SECONDS = 8
REGISTRY_CACHE_SECONDS = 300

# ---------------------------------------------------------------------------
# Shared SDK client  (auto-authenticates as the app SP)
# ---------------------------------------------------------------------------
_client: WorkspaceClient | None = None

def _sdk() -> WorkspaceClient:
    global _client
    if _client is None:
        _client = WorkspaceClient()
    return _client

# ---------------------------------------------------------------------------
# State mappings  (unchanged from the original file)
# ---------------------------------------------------------------------------
SUCCESS_STATES = {"SUCCESS", "SUCCEEDED", "SUCCESS_WITH_FAILURES"}
CANCELLED_STATES = {
    "CANCELED", "CANCELLED", "TIMEDOUT", "TIMED_OUT",
    "DISABLED", "EXCLUDED", "SKIPPED",
}

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


def _rs_str(rs) -> str | None:
    """Convert a RunResultState enum (or plain str) to a clean string."""
    if rs is None:
        return None
    return rs.value if hasattr(rs, "value") else str(rs)


def bucket_result_state(result_state: str) -> str:
    if result_state in SUCCESS_STATES:
        return "success"
    if result_state in CANCELLED_STATES:
        return "cancelled"
    return "failed"


def cause_label(termination_code: str | None) -> str:
    if not termination_code:
        return "Unknown"
    return TERMINATION_CODE_LABELS.get(
        termination_code,
        termination_code.replace("_", " ").title(),
    )


# ---------------------------------------------------------------------------
# Core data-fetch functions  (SDK replaces SQL / REST calls)
# ---------------------------------------------------------------------------
MAX_RUNS = 5000  # safety cap


@ttl_cache(CACHE_SECONDS)
def fetch_job_runs(days: int) -> list[dict]:
    """One row per completed job run in the last *days* days.

    Uses w.jobs.list_runs() — the SDK auto-paginates.  Each dict has
    the same keys the old system-table version returned so all downstream
    callers (build_daily_trend, summarize_run_status, …) work unchanged.
    """
    try:
        from_ms = int(
            (datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000
        )
        sdk = _sdk()

        mapped: list[dict] = []
        count = 0

        for run in sdk.jobs.list_runs(
            completed_only=True,
            start_time_from=from_ms,
        ):
            if count >= MAX_RUNS:
                break
            state = run.state
            rs = _rs_str(state.result_state) if state else None
            if not rs:
                continue

            start_ms = run.start_time
            end_ms = run.end_time
            mapped.append({
                "job_id": str(run.job_id),
                "run_id": str(run.run_id),
                "result_state": rs,
                "period_start_time": (
                    datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
                    if start_ms else None
                ),
                "period_end_time": (
                    datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc)
                    if end_ms else None
                ),
                "termination_code": rs,
            })
            count += 1

        return mapped

    except Exception:
        logger.warning("Could not fetch job runs via SDK", exc_info=True)
        return []


@ttl_cache(CACHE_SECONDS)
def fetch_running_job_count() -> int:
    """Count of job runs currently in progress."""
    try:
        sdk = _sdk()
        count = 0
        for _ in sdk.jobs.list_runs(active_only=True):
            count += 1
            if count >= MAX_RUNS:
                break
        return count
    except Exception:
        logger.warning("Could not count in-progress job runs via SDK", exc_info=True)
        return 0


@ttl_cache(REGISTRY_CACHE_SECONDS)
def fetch_job_registry() -> dict[int, dict]:
    """job_id → {"name", "tags"} for every job visible to the SP."""
    try:
        sdk = _sdk()
        registry: dict[int, dict] = {}
        for job in sdk.jobs.list():
            settings = job.settings
            name = (settings.name if settings else None) or f"job-{job.job_id}"
            tags = (settings.tags if settings else None) or {}
            registry[job.job_id] = {"name": name, "tags": tags}
        return registry
    except Exception:
        logger.warning("Could not list jobs via SDK", exc_info=True)
        return {}


# ---------------------------------------------------------------------------
# Helpers (unchanged)
# ---------------------------------------------------------------------------
def job_tag_label(tags: dict) -> str:
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
    return format_duration(
        datetime.fromtimestamp(start_ms / 1000),
        datetime.fromtimestamp(end_ms / 1000),
    )


def summarize_run_status(runs: list[dict]) -> dict:
    counts = {"success": 0, "failed": 0, "cancelled": 0}
    for r in runs:
        counts[bucket_result_state(r["result_state"])] += 1
    return {"total_runs": len(runs), **counts}


def build_daily_trend(runs: list[dict], days: int) -> list[dict]:
    """One point per day for the last *days* days (oldest first)."""
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
