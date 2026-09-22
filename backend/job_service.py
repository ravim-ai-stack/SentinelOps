"""
job_service.py — Job runs & registry via the Databricks REST API
================================================================

Purpose:
    Fetch Databricks Jobs and Job Runs that are visible to the
    CURRENT LOGGED-IN USER.

Authentication:

    Deployed Databricks App:
        Browser
            ↓
        Databricks SSO
            ↓
        x-forwarded-access-token
            ↓
        Databricks REST API
            ↓
        Databricks Jobs API

    Local development:
        No forwarded viewer token
            ↓
        WorkspaceClient()
            ↓
        Local DATABRICKS_* credentials

This file does NOT use system.lakeflow SQL tables.

The Jobs page therefore uses the logged-in user's Databricks
permissions rather than requiring the SentinelOps App Service
Principal to have CAN_VIEW on every Job.
"""

import logging
from datetime import date, datetime, timedelta, timezone

import requests
from fastapi import Request
from databricks.sdk import WorkspaceClient
from databricks.sdk.core import Config

logger = logging.getLogger("sentinelops.jobs")

MAX_RUNS = 5000


# ---------------------------------------------------------------------------
# SDK CLIENT
# ---------------------------------------------------------------------------

_client: WorkspaceClient | None = None


def _default_sdk() -> WorkspaceClient:
    """
    Default SDK client.

    Used when there is no forwarded viewer token.

    In local development this uses the local DATABRICKS_* credentials.

    In a deployed Databricks App this uses the App's own
    service-principal OAuth identity.
    """
    global _client

    if _client is None:
        _client = WorkspaceClient()

    return _client


# ---------------------------------------------------------------------------
# VIEWER REST CLIENT
# ---------------------------------------------------------------------------

def _viewer_token(request: Request) -> str | None:
    """
    Return the Databricks access token forwarded by Databricks Apps
    for the currently logged-in browser user.
    """
    return request.headers.get("x-forwarded-access-token")


def _workspace_host() -> str:
    """
    Get the Databricks workspace host.

    Config() is safe here because we are only reading the host.
    We do NOT create WorkspaceClient(token=viewer_token), which would
    cause the OAuth + PAT authorization conflict.
    """
    cfg = Config()
    return cfg.host.rstrip("/")


def _viewer_rest_get(
    request: Request,
    path: str,
    params: dict | None = None,
) -> dict:
    """
    Execute a Databricks REST API GET request as the current viewer.

    Uses:
        x-forwarded-access-token
            ↓
        Authorization: Bearer <viewer token>

    This avoids the Databricks SDK conflict where the App's OAuth
    credentials and the forwarded viewer token are both detected.
    """
    token = _viewer_token(request)

    if not token:
        raise RuntimeError(
            "x-forwarded-access-token is missing; "
            "viewer REST request cannot be performed"
        )

    response = requests.get(
        f"{_workspace_host()}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        params=params,
        timeout=30,
    )

    try:
        response.raise_for_status()
    except requests.HTTPError:
        logger.error(
            "Databricks REST API failed: status=%s path=%s body=%s",
            response.status_code,
            path,
            response.text[:2000],
        )
        raise

    return response.json()


# ---------------------------------------------------------------------------
# STATE MAPPINGS
# ---------------------------------------------------------------------------

SUCCESS_STATES = {
    "SUCCESS",
    "SUCCEEDED",
    "SUCCESS_WITH_FAILURES",
}

CANCELLED_STATES = {
    "CANCELED",
    "CANCELLED",
    "TIMEDOUT",
    "TIMED_OUT",
    "DISABLED",
    "EXCLUDED",
    "SKIPPED",
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
    "MAX_JOB_QUEUE_SIZE_EXCEEDED": "Max job queue size exceeded",
    "CLOUD_FAILURE": "Cloud provider failure",
}


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def _rs_str(rs) -> str | None:
    """Convert a Databricks SDK enum or string to a normal string."""
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
# JOB RUNS
# ---------------------------------------------------------------------------

def fetch_job_runs(
    request: Request,
    days: int,
) -> list[dict]:
    """
    Fetch completed Job Runs visible to the currently logged-in user.

    Deployed App:
        x-forwarded-access-token → Jobs REST API

    Local development:
        WorkspaceClient() → jobs.list_runs()
    """

    try:
        from_ms = int(
            (
                datetime.now(timezone.utc)
                - timedelta(days=days)
            ).timestamp()
            * 1000
        )

        viewer_token = _viewer_token(request)

        # ---------------------------------------------------------------
        # Viewer path — use REST API directly
        # ---------------------------------------------------------------
        if viewer_token:
            mapped: list[dict] = []
            count = 0

            page_token: str | None = None

            while True:
                params = {
                    "completed_only": "true",
                    "start_time_from": from_ms,
                    "limit": min(100, MAX_RUNS - count),
                }

                if page_token:
                    params["page_token"] = page_token

                data = _viewer_rest_get(
                    request,
                    "/api/2.1/jobs/runs/list",
                    params,
                )

                for run in data.get("runs", []):
                    if count >= MAX_RUNS:
                        break

                    state = run.get("state") or {}

                    result_state = (
                        state.get("result_state")
                        or state.get("life_cycle_state")
                    )

                    if not result_state:
                        continue

                    start_ms = run.get("start_time")
                    end_ms = run.get("end_time")

                    mapped.append(
                        {
                            "job_id": str(run.get("job_id")),
                            "run_id": str(run.get("run_id")),
                            "result_state": result_state,

                            "period_start_time": (
                                datetime.fromtimestamp(
                                    start_ms / 1000,
                                    tz=timezone.utc,
                                )
                                if start_ms
                                else None
                            ),

                            "period_end_time": (
                                datetime.fromtimestamp(
                                    end_ms / 1000,
                                    tz=timezone.utc,
                                )
                                if end_ms
                                else None
                            ),

                            "termination_code": (
                                state.get("termination_code")
                                or result_state
                            ),
                        }
                    )

                    count += 1

                if count >= MAX_RUNS:
                    break

                page_token = data.get("next_page_token")

                if not page_token:
                    break

            logger.info(
                "Fetched %d completed job runs for current viewer",
                len(mapped),
            )

            return mapped

        # ---------------------------------------------------------------
        # Local/default path — use SDK
        # ---------------------------------------------------------------

        sdk = _default_sdk()

        mapped: list[dict] = []
        count = 0

        for run in sdk.jobs.list_runs(
            completed_only=True,
            start_time_from=from_ms,
        ):
            if count >= MAX_RUNS:
                break

            state = run.state

            result_state = (
                _rs_str(state.result_state)
                if state
                else None
            )

            if not result_state:
                continue

            start_ms = run.start_time
            end_ms = run.end_time

            mapped.append(
                {
                    "job_id": str(run.job_id),
                    "run_id": str(run.run_id),
                    "result_state": result_state,

                    "period_start_time": (
                        datetime.fromtimestamp(
                            start_ms / 1000,
                            tz=timezone.utc,
                        )
                        if start_ms
                        else None
                    ),

                    "period_end_time": (
                        datetime.fromtimestamp(
                            end_ms / 1000,
                            tz=timezone.utc,
                        )
                        if end_ms
                        else None
                    ),

                    "termination_code": result_state,
                }
            )

            count += 1

        logger.info(
            "Fetched %d completed job runs using default identity",
            len(mapped),
        )

        return mapped

    except Exception:
        logger.warning(
            "Could not fetch job runs for current viewer",
            exc_info=True,
        )

        return []


# ---------------------------------------------------------------------------
# CURRENTLY RUNNING JOBS
# ---------------------------------------------------------------------------

def fetch_running_job_count(
    request: Request,
) -> int:
    """
    Count currently active Job Runs visible to the current user.
    """

    try:
        viewer_token = _viewer_token(request)

        # ---------------------------------------------------------------
        # Viewer path
        # ---------------------------------------------------------------
        if viewer_token:
            data = _viewer_rest_get(
                request,
                "/api/2.1/jobs/runs/list",
                {
                    "active_only": "true",
                    "limit": MAX_RUNS,
                },
            )

            count = len(data.get("runs", []))

            logger.info(
                "Current viewer has %d active job runs",
                count,
            )

            return count

        # ---------------------------------------------------------------
        # Local/default path
        # ---------------------------------------------------------------

        sdk = _default_sdk()

        count = 0

        for _ in sdk.jobs.list_runs(active_only=True):
            count += 1

            if count >= MAX_RUNS:
                break

        logger.info(
            "Default identity has %d active job runs",
            count,
        )

        return count

    except Exception:
        logger.warning(
            "Could not count active job runs for current viewer",
            exc_info=True,
        )

        return 0


# ---------------------------------------------------------------------------
# JOB REGISTRY
# ---------------------------------------------------------------------------

def fetch_job_registry(
    request: Request,
) -> dict[int, dict]:
    """
    Return a registry of Jobs visible to the current logged-in user.

    Result:

        {
            job_id: {
                "name": "...",
                "tags": {...}
            }
        }
    """

    try:
        viewer_token = _viewer_token(request)

        registry: dict[int, dict] = {}

        # ---------------------------------------------------------------
        # Viewer path — REST API
        # ---------------------------------------------------------------

        if viewer_token:
            page_token: str | None = None

            while True:
                params = {
                    "limit": 100,
                }

                if page_token:
                    params["page_token"] = page_token

                data = _viewer_rest_get(
                    request,
                    "/api/2.2/jobs/list",
                    params,
                )

                for job in data.get("jobs", []):
                    job_id = job.get("job_id")

                    if job_id is None:
                        continue

                    settings = job.get("settings") or {}

                    name = (
                        settings.get("name")
                        or f"job-{job_id}"
                    )

                    tags = settings.get("tags") or {}

                    registry[int(job_id)] = {
                        "name": name,
                        "tags": tags,
                    }

                page_token = data.get("next_page_token")

                if not page_token:
                    break

            logger.info(
                "Fetched %d Jobs visible to current viewer",
                len(registry),
            )

            return registry

        # ---------------------------------------------------------------
        # Local/default path — SDK
        # ---------------------------------------------------------------

        sdk = _default_sdk()

        for job in sdk.jobs.list():
            settings = job.settings

            name = (
                (settings.name if settings else None)
                or f"job-{job.job_id}"
            )

            tags = (
                (settings.tags if settings else None)
                or {}
            )

            registry[job.job_id] = {
                "name": name,
                "tags": tags,
            }

        logger.info(
            "Fetched %d Jobs using default identity",
            len(registry),
        )

        return registry

    except Exception:
        logger.warning(
            "Could not list Jobs for current viewer",
            exc_info=True,
        )

        return {}


# ---------------------------------------------------------------------------
# SIMPLE JOB LIST
# ---------------------------------------------------------------------------

def fetch_jobs(
    request: Request,
) -> list[dict]:
    """
    Return a simple list of Jobs visible to the current logged-in user.

    Useful for the SentinelOps Jobs page.
    """

    try:
        viewer_token = _viewer_token(request)

        jobs: list[dict] = []

        # ---------------------------------------------------------------
        # Viewer path — REST API
        # ---------------------------------------------------------------

        if viewer_token:
            page_token: str | None = None

            while True:
                params = {
                    "limit": 100,
                }

                if page_token:
                    params["page_token"] = page_token

                data = _viewer_rest_get(
                    request,
                    "/api/2.2/jobs/list",
                    params,
                )

                for job in data.get("jobs", []):
                    job_id = job.get("job_id")

                    if job_id is None:
                        continue

                    settings = job.get("settings") or {}

                    name = (
                        settings.get("name")
                        or f"job-{job_id}"
                    )

                    tags = settings.get("tags") or {}

                    jobs.append(
                        {
                            "job_id": str(job_id),
                            "name": name,
                            "tags": tags,
                        }
                    )

                page_token = data.get("next_page_token")

                if not page_token:
                    break

            logger.info(
                "Fetched %d Jobs for current viewer",
                len(jobs),
            )

            return jobs

        # ---------------------------------------------------------------
        # Local/default path — SDK
        # ---------------------------------------------------------------

        sdk = _default_sdk()

        for job in sdk.jobs.list():
            settings = job.settings

            name = (
                (settings.name if settings else None)
                or f"job-{job.job_id}"
            )

            tags = (
                (settings.tags if settings else None)
                or {}
            )

            jobs.append(
                {
                    "job_id": str(job.job_id),
                    "name": name,
                    "tags": tags,
                }
            )

        logger.info(
            "Fetched %d Jobs using default identity",
            len(jobs),
        )

        return jobs

    except Exception:
        logger.warning(
            "Could not fetch Jobs for current viewer",
            exc_info=True,
        )

        return []


# ---------------------------------------------------------------------------
# FORMATTING HELPERS
# ---------------------------------------------------------------------------

def job_tag_label(tags: dict) -> str:
    for key in (
        "team",
        "project",
        "department",
        "cost_center",
        "domain",
    ):
        if tags.get(key):
            return tags[key]

    if tags:
        return next(iter(tags.values()))

    return "General"


def format_timestamp(
    dt: datetime | None,
) -> str:
    if not dt:
        return "–"

    dt = dt.astimezone()

    hour12 = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"

    return (
        f"{dt.strftime('%b')} "
        f"{dt.day}, "
        f"{dt.year} "
        f"{hour12:02d}:{dt.minute:02d} "
        f"{ampm}"
    )


def format_duration(
    start: datetime | None,
    end: datetime | None,
) -> str:

    if not start or not end:
        return "–"

    seconds = max(
        0,
        int((end - start).total_seconds()),
    )

    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)

    if h:
        return f"{h}h {m}m {s}s"

    if m:
        return f"{m}m {s}s"

    return f"{s}s"


def format_timestamp_ms(
    ms: int | None,
) -> str:

    if not ms:
        return "–"

    return format_timestamp(
        datetime.fromtimestamp(ms / 1000)
    )


def format_duration_ms(
    start_ms: int | None,
    end_ms: int | None,
) -> str:

    if not start_ms or not end_ms:
        return "–"

    return format_duration(
        datetime.fromtimestamp(start_ms / 1000),
        datetime.fromtimestamp(end_ms / 1000),
    )


# ---------------------------------------------------------------------------
# RUN SUMMARIES
# ---------------------------------------------------------------------------

def summarize_run_status(
    runs: list[dict],
) -> dict:

    counts = {
        "success": 0,
        "failed": 0,
        "cancelled": 0,
    }

    for r in runs:

        counts[
            bucket_result_state(
                r["result_state"]
            )
        ] += 1

    return {
        "total_runs": len(runs),
        **counts,
    }


def build_daily_trend(
    runs: list[dict],
    days: int,
) -> list[dict]:
    """
    One point per day for the last *days* days.
    Oldest day first.
    """

    today = date.today()

    buckets: dict[date, dict] = {
        today - timedelta(days=offset): {
            "success": 0,
            "failed": 0,
            "cancelled": 0,
        }
        for offset in range(
            days - 1,
            -1,
            -1,
        )
    }

    for r in runs:

        if not r.get("period_start_time"):
            continue

        run_date = r["period_start_time"].date()

        bucket = buckets.get(run_date)

        if bucket is not None:

            bucket[
                bucket_result_state(
                    r["result_state"]
                )
            ] += 1

    return [
        {
            "label": f"{d.strftime('%b')} {d.day}",
            **counts,
        }
        for d, counts in buckets.items()
    ]