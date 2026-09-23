# """
# job_service.py — Job runs & registry via the Databricks REST API
# ================================================================

# Purpose:
#     Fetch Databricks Jobs and Job Runs that are visible to the
#     CURRENT LOGGED-IN USER.

# Authentication:

#     Deployed Databricks App:
#         Browser
#             ↓
#         Databricks SSO
#             ↓
#         x-forwarded-access-token
#             ↓
#         Databricks REST API
#             ↓
#         Databricks Jobs API

#     Local development:
#         No forwarded viewer token
#             ↓
#         WorkspaceClient()
#             ↓
#         Local DATABRICKS_* credentials

# This file does NOT use system.lakeflow SQL tables.

# The Jobs page therefore uses the logged-in user's Databricks
# permissions rather than requiring the SentinelOps App Service
# Principal to have CAN_VIEW on every Job.
# """

# import logging
# from datetime import date, datetime, timedelta, timezone

# import requests
# from fastapi import Request
# from databricks.sdk import WorkspaceClient
# from databricks.sdk.core import Config

# logger = logging.getLogger("sentinelops.jobs")

# MAX_RUNS = 5000


# # ---------------------------------------------------------------------------
# # SDK CLIENT
# # ---------------------------------------------------------------------------

# _client: WorkspaceClient | None = None


# def _default_sdk() -> WorkspaceClient:
#     """
#     Default SDK client.

#     Used when there is no forwarded viewer token.

#     In local development this uses the local DATABRICKS_* credentials.

#     In a deployed Databricks App this uses the App's own
#     service-principal OAuth identity.
#     """
#     global _client

#     if _client is None:
#         _client = WorkspaceClient()

#     return _client


# # ---------------------------------------------------------------------------
# # VIEWER REST CLIENT
# # ---------------------------------------------------------------------------

# def _viewer_token(request: Request) -> str | None:
#     """
#     Return the Databricks access token forwarded by Databricks Apps
#     for the currently logged-in browser user.
#     """
#     return request.headers.get("x-forwarded-access-token")


# def _workspace_host() -> str:
#     """
#     Get the Databricks workspace host.

#     Config() is safe here because we are only reading the host.
#     We do NOT create WorkspaceClient(token=viewer_token), which would
#     cause the OAuth + PAT authorization conflict.
#     """
#     cfg = Config()
#     return cfg.host.rstrip("/")


# def _viewer_rest_get(
#     request: Request,
#     path: str,
#     params: dict | None = None,
# ) -> dict:
#     """
#     Execute a Databricks REST API GET request as the current viewer.

#     Uses:
#         x-forwarded-access-token
#             ↓
#         Authorization: Bearer <viewer token>

#     This avoids the Databricks SDK conflict where the App's OAuth
#     credentials and the forwarded viewer token are both detected.
#     """
#     token = _viewer_token(request)

#     if not token:
#         raise RuntimeError(
#             "x-forwarded-access-token is missing; "
#             "viewer REST request cannot be performed"
#         )

#     response = requests.get(
#         f"{_workspace_host()}{path}",
#         headers={
#             "Authorization": f"Bearer {token}",
#             "Accept": "application/json",
#         },
#         params=params,
#         timeout=30,
#     )

#     try:
#         response.raise_for_status()
#     except requests.HTTPError:
#         logger.error(
#             "Databricks REST API failed: status=%s path=%s body=%s",
#             response.status_code,
#             path,
#             response.text[:2000],
#         )
#         raise

#     return response.json()


# # ---------------------------------------------------------------------------
# # STATE MAPPINGS
# # ---------------------------------------------------------------------------

# SUCCESS_STATES = {
#     "SUCCESS",
#     "SUCCEEDED",
#     "SUCCESS_WITH_FAILURES",
# }

# CANCELLED_STATES = {
#     "CANCELED",
#     "CANCELLED",
#     "TIMEDOUT",
#     "TIMED_OUT",
#     "DISABLED",
#     "EXCLUDED",
#     "SKIPPED",
# }

# TERMINATION_CODE_LABELS = {
#     "SUCCESS": "Success",
#     "CANCELED": "Cancelled by user",
#     "USER_CANCELED": "Cancelled by user",
#     "SKIPPED": "Skipped",
#     "INTERNAL_ERROR": "Internal platform error",
#     "DRIVER_ERROR": "Driver error",
#     "CLUSTER_ERROR": "Cluster provisioning error",
#     "REPOSITORY_CHECKOUT_FAILED": "Repo checkout failed",
#     "INVALID_CLUSTER_REQUEST": "Invalid cluster config",
#     "WORKSPACE_RUN_LIMIT_EXCEEDED": "Workspace run limit exceeded",
#     "FEATURE_DISABLED": "Feature disabled",
#     "CLUSTER_REQUEST_LIMIT_EXCEEDED": "Cluster request limit exceeded",
#     "STORAGE_ACCESS_ERROR": "Storage access error",
#     "RUN_EXECUTION_ERROR": "Task execution error",
#     "UNAUTHORIZED_ERROR": "Permission denied",
#     "LIBRARY_INSTALLATION_ERROR": "Library install failed",
#     "MAX_CONCURRENT_RUNS_EXCEEDED": "Max concurrent runs exceeded",
#     "MAX_JOB_QUEUE_SIZE_EXCEEDED": "Max job queue size exceeded",
#     "CLOUD_FAILURE": "Cloud provider failure",
# }


# # ---------------------------------------------------------------------------
# # HELPERS
# # ---------------------------------------------------------------------------

# def _rs_str(rs) -> str | None:
#     """Convert a Databricks SDK enum or string to a normal string."""
#     if rs is None:
#         return None

#     return rs.value if hasattr(rs, "value") else str(rs)


# def bucket_result_state(result_state: str) -> str:
#     if result_state in SUCCESS_STATES:
#         return "success"

#     if result_state in CANCELLED_STATES:
#         return "cancelled"

#     return "failed"


# def cause_label(termination_code: str | None) -> str:
#     if not termination_code:
#         return "Unknown"

#     return TERMINATION_CODE_LABELS.get(
#         termination_code,
#         termination_code.replace("_", " ").title(),
#     )


# # ---------------------------------------------------------------------------
# # JOB RUNS
# # ---------------------------------------------------------------------------

# def fetch_job_runs(
#     request: Request,
#     days: int,
# ) -> list[dict]:
#     """
#     Fetch completed Job Runs visible to the currently logged-in user.

#     Deployed App:
#         x-forwarded-access-token → Jobs REST API

#     Local development:
#         WorkspaceClient() → jobs.list_runs()
#     """

#     try:
#         from_ms = int(
#             (
#                 datetime.now(timezone.utc)
#                 - timedelta(days=days)
#             ).timestamp()
#             * 1000
#         )

#         viewer_token = _viewer_token(request)

#         # ---------------------------------------------------------------
#         # Viewer path — use REST API directly
#         # ---------------------------------------------------------------
#         if viewer_token:
#             mapped: list[dict] = []
#             count = 0

#             page_token: str | None = None

#             while True:
#                 params = {
#                     "completed_only": "true",
#                     "start_time_from": from_ms,
#                     "limit": min(100, MAX_RUNS - count),
#                 }

#                 if page_token:
#                     params["page_token"] = page_token

#                 data = _viewer_rest_get(
#                     request,
#                     "/api/2.1/jobs/runs/list",
#                     params,
#                 )

#                 for run in data.get("runs", []):
#                     if count >= MAX_RUNS:
#                         break

#                     state = run.get("state") or {}

#                     result_state = (
#                         state.get("result_state")
#                         or state.get("life_cycle_state")
#                     )

#                     if not result_state:
#                         continue

#                     start_ms = run.get("start_time")
#                     end_ms = run.get("end_time")

#                     mapped.append(
#                         {
#                             "job_id": str(run.get("job_id")),
#                             "run_id": str(run.get("run_id")),
#                             "result_state": result_state,

#                             "period_start_time": (
#                                 datetime.fromtimestamp(
#                                     start_ms / 1000,
#                                     tz=timezone.utc,
#                                 )
#                                 if start_ms
#                                 else None
#                             ),

#                             "period_end_time": (
#                                 datetime.fromtimestamp(
#                                     end_ms / 1000,
#                                     tz=timezone.utc,
#                                 )
#                                 if end_ms
#                                 else None
#                             ),

#                             "termination_code": (
#                                 state.get("termination_code")
#                                 or result_state
#                             ),
#                         }
#                     )

#                     count += 1

#                 if count >= MAX_RUNS:
#                     break

#                 page_token = data.get("next_page_token")

#                 if not page_token:
#                     break

#             logger.info(
#                 "Fetched %d completed job runs for current viewer",
#                 len(mapped),
#             )

#             return mapped

#         # ---------------------------------------------------------------
#         # Local/default path — use SDK
#         # ---------------------------------------------------------------

#         sdk = _default_sdk()

#         mapped: list[dict] = []
#         count = 0

#         for run in sdk.jobs.list_runs(
#             completed_only=True,
#             start_time_from=from_ms,
#         ):
#             if count >= MAX_RUNS:
#                 break

#             state = run.state

#             result_state = (
#                 _rs_str(state.result_state)
#                 if state
#                 else None
#             )

#             if not result_state:
#                 continue

#             start_ms = run.start_time
#             end_ms = run.end_time

#             mapped.append(
#                 {
#                     "job_id": str(run.job_id),
#                     "run_id": str(run.run_id),
#                     "result_state": result_state,

#                     "period_start_time": (
#                         datetime.fromtimestamp(
#                             start_ms / 1000,
#                             tz=timezone.utc,
#                         )
#                         if start_ms
#                         else None
#                     ),

#                     "period_end_time": (
#                         datetime.fromtimestamp(
#                             end_ms / 1000,
#                             tz=timezone.utc,
#                         )
#                         if end_ms
#                         else None
#                     ),

#                     "termination_code": result_state,
#                 }
#             )

#             count += 1

#         logger.info(
#             "Fetched %d completed job runs using default identity",
#             len(mapped),
#         )

#         return mapped

#     except Exception:
#         logger.warning(
#             "Could not fetch job runs for current viewer",
#             exc_info=True,
#         )

#         return []


# # ---------------------------------------------------------------------------
# # CURRENTLY RUNNING JOBS
# # ---------------------------------------------------------------------------

# def fetch_running_job_count(
#     request: Request,
# ) -> int:
#     """
#     Count currently active Job Runs visible to the current user.
#     """

#     try:
#         viewer_token = _viewer_token(request)

#         # ---------------------------------------------------------------
#         # Viewer path
#         # ---------------------------------------------------------------
#         if viewer_token:
#             data = _viewer_rest_get(
#                 request,
#                 "/api/2.1/jobs/runs/list",
#                 {
#                     "active_only": "true",
#                     "limit": MAX_RUNS,
#                 },
#             )

#             count = len(data.get("runs", []))

#             logger.info(
#                 "Current viewer has %d active job runs",
#                 count,
#             )

#             return count

#         # ---------------------------------------------------------------
#         # Local/default path
#         # ---------------------------------------------------------------

#         sdk = _default_sdk()

#         count = 0

#         for _ in sdk.jobs.list_runs(active_only=True):
#             count += 1

#             if count >= MAX_RUNS:
#                 break

#         logger.info(
#             "Default identity has %d active job runs",
#             count,
#         )

#         return count

#     except Exception:
#         logger.warning(
#             "Could not count active job runs for current viewer",
#             exc_info=True,
#         )

#         return 0


# # ---------------------------------------------------------------------------
# # JOB REGISTRY
# # ---------------------------------------------------------------------------

# def fetch_job_registry(
#     request: Request,
# ) -> dict[int, dict]:
#     """
#     Return a registry of Jobs visible to the current logged-in user.

#     Result:

#         {
#             job_id: {
#                 "name": "...",
#                 "tags": {...}
#             }
#         }
#     """

#     try:
#         viewer_token = _viewer_token(request)

#         registry: dict[int, dict] = {}

#         # ---------------------------------------------------------------
#         # Viewer path — REST API
#         # ---------------------------------------------------------------

#         if viewer_token:
#             page_token: str | None = None

#             while True:
#                 params = {
#                     "limit": 100,
#                 }

#                 if page_token:
#                     params["page_token"] = page_token

#                 data = _viewer_rest_get(
#                     request,
#                     "/api/2.2/jobs/list",
#                     params,
#                 )

#                 for job in data.get("jobs", []):
#                     job_id = job.get("job_id")

#                     if job_id is None:
#                         continue

#                     settings = job.get("settings") or {}

#                     name = (
#                         settings.get("name")
#                         or f"job-{job_id}"
#                     )

#                     tags = settings.get("tags") or {}

#                     registry[int(job_id)] = {
#                         "name": name,
#                         "tags": tags,
#                     }

#                 page_token = data.get("next_page_token")

#                 if not page_token:
#                     break

#             logger.info(
#                 "Fetched %d Jobs visible to current viewer",
#                 len(registry),
#             )

#             return registry

#         # ---------------------------------------------------------------
#         # Local/default path — SDK
#         # ---------------------------------------------------------------

#         sdk = _default_sdk()

#         for job in sdk.jobs.list():
#             settings = job.settings

#             name = (
#                 (settings.name if settings else None)
#                 or f"job-{job.job_id}"
#             )

#             tags = (
#                 (settings.tags if settings else None)
#                 or {}
#             )

#             registry[job.job_id] = {
#                 "name": name,
#                 "tags": tags,
#             }

#         logger.info(
#             "Fetched %d Jobs using default identity",
#             len(registry),
#         )

#         return registry

#     except Exception:
#         logger.warning(
#             "Could not list Jobs for current viewer",
#             exc_info=True,
#         )

#         return {}


# # ---------------------------------------------------------------------------
# # SIMPLE JOB LIST
# # ---------------------------------------------------------------------------

# def fetch_jobs(
#     request: Request,
# ) -> list[dict]:
#     """
#     Return a simple list of Jobs visible to the current logged-in user.

#     Useful for the SentinelOps Jobs page.
#     """

#     try:
#         viewer_token = _viewer_token(request)

#         jobs: list[dict] = []

#         # ---------------------------------------------------------------
#         # Viewer path — REST API
#         # ---------------------------------------------------------------

#         if viewer_token:
#             page_token: str | None = None

#             while True:
#                 params = {
#                     "limit": 100,
#                 }

#                 if page_token:
#                     params["page_token"] = page_token

#                 data = _viewer_rest_get(
#                     request,
#                     "/api/2.2/jobs/list",
#                     params,
#                 )

#                 for job in data.get("jobs", []):
#                     job_id = job.get("job_id")

#                     if job_id is None:
#                         continue

#                     settings = job.get("settings") or {}

#                     name = (
#                         settings.get("name")
#                         or f"job-{job_id}"
#                     )

#                     tags = settings.get("tags") or {}

#                     jobs.append(
#                         {
#                             "job_id": str(job_id),
#                             "name": name,
#                             "tags": tags,
#                         }
#                     )

#                 page_token = data.get("next_page_token")

#                 if not page_token:
#                     break

#             logger.info(
#                 "Fetched %d Jobs for current viewer",
#                 len(jobs),
#             )

#             return jobs

#         # ---------------------------------------------------------------
#         # Local/default path — SDK
#         # ---------------------------------------------------------------

#         sdk = _default_sdk()

#         for job in sdk.jobs.list():
#             settings = job.settings

#             name = (
#                 (settings.name if settings else None)
#                 or f"job-{job.job_id}"
#             )

#             tags = (
#                 (settings.tags if settings else None)
#                 or {}
#             )

#             jobs.append(
#                 {
#                     "job_id": str(job.job_id),
#                     "name": name,
#                     "tags": tags,
#                 }
#             )

#         logger.info(
#             "Fetched %d Jobs using default identity",
#             len(jobs),
#         )

#         return jobs

#     except Exception:
#         logger.warning(
#             "Could not fetch Jobs for current viewer",
#             exc_info=True,
#         )

#         return []


# # ---------------------------------------------------------------------------
# # FORMATTING HELPERS
# # ---------------------------------------------------------------------------

# def job_tag_label(tags: dict) -> str:
#     for key in (
#         "team",
#         "project",
#         "department",
#         "cost_center",
#         "domain",
#     ):
#         if tags.get(key):
#             return tags[key]

#     if tags:
#         return next(iter(tags.values()))

#     return "General"


# def format_timestamp(
#     dt: datetime | None,
# ) -> str:
#     if not dt:
#         return "–"

#     dt = dt.astimezone()

#     hour12 = dt.hour % 12 or 12
#     ampm = "AM" if dt.hour < 12 else "PM"

#     return (
#         f"{dt.strftime('%b')} "
#         f"{dt.day}, "
#         f"{dt.year} "
#         f"{hour12:02d}:{dt.minute:02d} "
#         f"{ampm}"
#     )


# def format_duration(
#     start: datetime | None,
#     end: datetime | None,
# ) -> str:

#     if not start or not end:
#         return "–"

#     seconds = max(
#         0,
#         int((end - start).total_seconds()),
#     )

#     h, rem = divmod(seconds, 3600)
#     m, s = divmod(rem, 60)

#     if h:
#         return f"{h}h {m}m {s}s"

#     if m:
#         return f"{m}m {s}s"

#     return f"{s}s"


# def format_timestamp_ms(
#     ms: int | None,
# ) -> str:

#     if not ms:
#         return "–"

#     return format_timestamp(
#         datetime.fromtimestamp(ms / 1000)
#     )


# def format_duration_ms(
#     start_ms: int | None,
#     end_ms: int | None,
# ) -> str:

#     if not start_ms or not end_ms:
#         return "–"

#     return format_duration(
#         datetime.fromtimestamp(start_ms / 1000),
#         datetime.fromtimestamp(end_ms / 1000),
#     )


# # ---------------------------------------------------------------------------
# # RUN SUMMARIES
# # ---------------------------------------------------------------------------

# def summarize_run_status(
#     runs: list[dict],
# ) -> dict:

#     counts = {
#         "success": 0,
#         "failed": 0,
#         "cancelled": 0,
#     }

#     for r in runs:

#         counts[
#             bucket_result_state(
#                 r["result_state"]
#             )
#         ] += 1

#     return {
#         "total_runs": len(runs),
#         **counts,
#     }


# def build_daily_trend(
#     runs: list[dict],
#     days: int,
# ) -> list[dict]:
#     """
#     One point per day for the last *days* days.
#     Oldest day first.
#     """

#     today = date.today()

#     buckets: dict[date, dict] = {
#         today - timedelta(days=offset): {
#             "success": 0,
#             "failed": 0,
#             "cancelled": 0,
#         }
#         for offset in range(
#             days - 1,
#             -1,
#             -1,
#         )
#     }

#     for r in runs:

#         if not r.get("period_start_time"):
#             continue

#         run_date = r["period_start_time"].date()

#         bucket = buckets.get(run_date)

#         if bucket is not None:

#             bucket[
#                 bucket_result_state(
#                     r["result_state"]
#                 )
#             ] += 1

#     return [
#         {
#             "label": f"{d.strftime('%b')} {d.day}",
#             **counts,
#         }
#         for d, counts in buckets.items()
#     ]

"""
job_service.py — Job runs & registry via system.lakeflow system tables
======================================================================

Purpose:
    Fetch Databricks Jobs and Job Runs for the SentinelOps Jobs page.

Why not the Jobs REST API:

    Databricks Apps user authorization (x-forwarded-access-token) only
    supports a fixed set of OAuth scopes - sql, files, genie,
    model-serving, postgres, vector-search, apps, ai-gateway, plus the
    catalog.* / workspace.workspace SDK scopes. There is NO "jobs"
    scope, so calling /api/2.1/jobs/runs/list with the forwarded viewer
    token always fails with:

        403  Invalid scope, required scopes: jobs

    Calling the same endpoint with the App's service principal works
    only if that SP holds CAN_VIEW on every Job, which we cannot grant.

What we do instead:

    Read run history from the system tables, through the SQL Warehouse
    connection that databricks_client.py already pools:

        system.lakeflow.job_run_timeline   -> runs, states, timings
        system.lakeflow.jobs               -> names, tags, owners

    Access is governed by a single Unity Catalog grant on the schema
    rather than per-job ACLs:

        GRANT USE CATALOG ON CATALOG system TO `<app-service-principal>`;
        GRANT USE SCHEMA  ON SCHEMA system.lakeflow TO `<app-service-principal>`;
        GRANT SELECT      ON SCHEMA system.lakeflow TO `<app-service-principal>`;

Trade-offs you should know about:

    * system.lakeflow is account-wide within the cloud region, so it is
      NOT filtered by per-job permissions. Anyone who can open this app
      sees every job in the workspace. Set DATABRICKS_WORKSPACE_ID to at
      least confine results to this workspace.
    * Rows land with roughly 10-15 minutes of lag, so very recent runs
      may be missing and in-flight runs are approximate.
    * Retention is 365 days.
    * The tables are unavailable in some regions (e.g. asia-south1).

The `request` argument on the public functions is retained so the
existing routers keep working unchanged. It is no longer used.
"""

# """
# job_service.py — Job runs & registry via the Databricks REST API
# ================================================================

# Purpose:
#     Fetch Databricks Jobs and Job Runs that are visible to the
#     CURRENT LOGGED-IN USER.

# Authentication:

#     Deployed Databricks App:
#         Browser
#             ↓
#         Databricks SSO
#             ↓
#         x-forwarded-access-token
#             ↓
#         Databricks REST API
#             ↓
#         Databricks Jobs API

#     Local development:
#         No forwarded viewer token
#             ↓
#         WorkspaceClient()
#             ↓
#         Local DATABRICKS_* credentials

# This file does NOT use system.lakeflow SQL tables.

# The Jobs page therefore uses the logged-in user's Databricks
# permissions rather than requiring the SentinelOps App Service
# Principal to have CAN_VIEW on every Job.
# """

# import logging
# from datetime import date, datetime, timedelta, timezone

# import requests
# from fastapi import Request
# from databricks.sdk import WorkspaceClient
# from databricks.sdk.core import Config

# logger = logging.getLogger("sentinelops.jobs")

# MAX_RUNS = 5000


# # ---------------------------------------------------------------------------
# # SDK CLIENT
# # ---------------------------------------------------------------------------

# _client: WorkspaceClient | None = None


# def _default_sdk() -> WorkspaceClient:
#     """
#     Default SDK client.

#     Used when there is no forwarded viewer token.

#     In local development this uses the local DATABRICKS_* credentials.

#     In a deployed Databricks App this uses the App's own
#     service-principal OAuth identity.
#     """
#     global _client

#     if _client is None:
#         _client = WorkspaceClient()

#     return _client


# # ---------------------------------------------------------------------------
# # VIEWER REST CLIENT
# # ---------------------------------------------------------------------------

# def _viewer_token(request: Request) -> str | None:
#     """
#     Return the Databricks access token forwarded by Databricks Apps
#     for the currently logged-in browser user.
#     """
#     return request.headers.get("x-forwarded-access-token")


# def _workspace_host() -> str:
#     """
#     Get the Databricks workspace host.

#     Config() is safe here because we are only reading the host.
#     We do NOT create WorkspaceClient(token=viewer_token), which would
#     cause the OAuth + PAT authorization conflict.
#     """
#     cfg = Config()
#     return cfg.host.rstrip("/")


# def _viewer_rest_get(
#     request: Request,
#     path: str,
#     params: dict | None = None,
# ) -> dict:
#     """
#     Execute a Databricks REST API GET request as the current viewer.

#     Uses:
#         x-forwarded-access-token
#             ↓
#         Authorization: Bearer <viewer token>

#     This avoids the Databricks SDK conflict where the App's OAuth
#     credentials and the forwarded viewer token are both detected.
#     """
#     token = _viewer_token(request)

#     if not token:
#         raise RuntimeError(
#             "x-forwarded-access-token is missing; "
#             "viewer REST request cannot be performed"
#         )

#     response = requests.get(
#         f"{_workspace_host()}{path}",
#         headers={
#             "Authorization": f"Bearer {token}",
#             "Accept": "application/json",
#         },
#         params=params,
#         timeout=30,
#     )

#     try:
#         response.raise_for_status()
#     except requests.HTTPError:
#         logger.error(
#             "Databricks REST API failed: status=%s path=%s body=%s",
#             response.status_code,
#             path,
#             response.text[:2000],
#         )
#         raise

#     return response.json()


# # ---------------------------------------------------------------------------
# # STATE MAPPINGS
# # ---------------------------------------------------------------------------

# SUCCESS_STATES = {
#     "SUCCESS",
#     "SUCCEEDED",
#     "SUCCESS_WITH_FAILURES",
# }

# CANCELLED_STATES = {
#     "CANCELED",
#     "CANCELLED",
#     "TIMEDOUT",
#     "TIMED_OUT",
#     "DISABLED",
#     "EXCLUDED",
#     "SKIPPED",
# }

# TERMINATION_CODE_LABELS = {
#     "SUCCESS": "Success",
#     "CANCELED": "Cancelled by user",
#     "USER_CANCELED": "Cancelled by user",
#     "SKIPPED": "Skipped",
#     "INTERNAL_ERROR": "Internal platform error",
#     "DRIVER_ERROR": "Driver error",
#     "CLUSTER_ERROR": "Cluster provisioning error",
#     "REPOSITORY_CHECKOUT_FAILED": "Repo checkout failed",
#     "INVALID_CLUSTER_REQUEST": "Invalid cluster config",
#     "WORKSPACE_RUN_LIMIT_EXCEEDED": "Workspace run limit exceeded",
#     "FEATURE_DISABLED": "Feature disabled",
#     "CLUSTER_REQUEST_LIMIT_EXCEEDED": "Cluster request limit exceeded",
#     "STORAGE_ACCESS_ERROR": "Storage access error",
#     "RUN_EXECUTION_ERROR": "Task execution error",
#     "UNAUTHORIZED_ERROR": "Permission denied",
#     "LIBRARY_INSTALLATION_ERROR": "Library install failed",
#     "MAX_CONCURRENT_RUNS_EXCEEDED": "Max concurrent runs exceeded",
#     "MAX_JOB_QUEUE_SIZE_EXCEEDED": "Max job queue size exceeded",
#     "CLOUD_FAILURE": "Cloud provider failure",
# }


# # ---------------------------------------------------------------------------
# # HELPERS
# # ---------------------------------------------------------------------------

# def _rs_str(rs) -> str | None:
#     """Convert a Databricks SDK enum or string to a normal string."""
#     if rs is None:
#         return None

#     return rs.value if hasattr(rs, "value") else str(rs)


# def bucket_result_state(result_state: str) -> str:
#     if result_state in SUCCESS_STATES:
#         return "success"

#     if result_state in CANCELLED_STATES:
#         return "cancelled"

#     return "failed"


# def cause_label(termination_code: str | None) -> str:
#     if not termination_code:
#         return "Unknown"

#     return TERMINATION_CODE_LABELS.get(
#         termination_code,
#         termination_code.replace("_", " ").title(),
#     )


# # ---------------------------------------------------------------------------
# # JOB RUNS
# # ---------------------------------------------------------------------------

# def fetch_job_runs(
#     request: Request,
#     days: int,
# ) -> list[dict]:
#     """
#     Fetch completed Job Runs visible to the currently logged-in user.

#     Deployed App:
#         x-forwarded-access-token → Jobs REST API

#     Local development:
#         WorkspaceClient() → jobs.list_runs()
#     """

#     try:
#         from_ms = int(
#             (
#                 datetime.now(timezone.utc)
#                 - timedelta(days=days)
#             ).timestamp()
#             * 1000
#         )

#         viewer_token = _viewer_token(request)

#         # ---------------------------------------------------------------
#         # Viewer path — use REST API directly
#         # ---------------------------------------------------------------
#         if viewer_token:
#             mapped: list[dict] = []
#             count = 0

#             page_token: str | None = None

#             while True:
#                 params = {
#                     "completed_only": "true",
#                     "start_time_from": from_ms,
#                     "limit": min(100, MAX_RUNS - count),
#                 }

#                 if page_token:
#                     params["page_token"] = page_token

#                 data = _viewer_rest_get(
#                     request,
#                     "/api/2.1/jobs/runs/list",
#                     params,
#                 )

#                 for run in data.get("runs", []):
#                     if count >= MAX_RUNS:
#                         break

#                     state = run.get("state") or {}

#                     result_state = (
#                         state.get("result_state")
#                         or state.get("life_cycle_state")
#                     )

#                     if not result_state:
#                         continue

#                     start_ms = run.get("start_time")
#                     end_ms = run.get("end_time")

#                     mapped.append(
#                         {
#                             "job_id": str(run.get("job_id")),
#                             "run_id": str(run.get("run_id")),
#                             "result_state": result_state,

#                             "period_start_time": (
#                                 datetime.fromtimestamp(
#                                     start_ms / 1000,
#                                     tz=timezone.utc,
#                                 )
#                                 if start_ms
#                                 else None
#                             ),

#                             "period_end_time": (
#                                 datetime.fromtimestamp(
#                                     end_ms / 1000,
#                                     tz=timezone.utc,
#                                 )
#                                 if end_ms
#                                 else None
#                             ),

#                             "termination_code": (
#                                 state.get("termination_code")
#                                 or result_state
#                             ),
#                         }
#                     )

#                     count += 1

#                 if count >= MAX_RUNS:
#                     break

#                 page_token = data.get("next_page_token")

#                 if not page_token:
#                     break

#             logger.info(
#                 "Fetched %d completed job runs for current viewer",
#                 len(mapped),
#             )

#             return mapped

#         # ---------------------------------------------------------------
#         # Local/default path — use SDK
#         # ---------------------------------------------------------------

#         sdk = _default_sdk()

#         mapped: list[dict] = []
#         count = 0

#         for run in sdk.jobs.list_runs(
#             completed_only=True,
#             start_time_from=from_ms,
#         ):
#             if count >= MAX_RUNS:
#                 break

#             state = run.state

#             result_state = (
#                 _rs_str(state.result_state)
#                 if state
#                 else None
#             )

#             if not result_state:
#                 continue

#             start_ms = run.start_time
#             end_ms = run.end_time

#             mapped.append(
#                 {
#                     "job_id": str(run.job_id),
#                     "run_id": str(run.run_id),
#                     "result_state": result_state,

#                     "period_start_time": (
#                         datetime.fromtimestamp(
#                             start_ms / 1000,
#                             tz=timezone.utc,
#                         )
#                         if start_ms
#                         else None
#                     ),

#                     "period_end_time": (
#                         datetime.fromtimestamp(
#                             end_ms / 1000,
#                             tz=timezone.utc,
#                         )
#                         if end_ms
#                         else None
#                     ),

#                     "termination_code": result_state,
#                 }
#             )

#             count += 1

#         logger.info(
#             "Fetched %d completed job runs using default identity",
#             len(mapped),
#         )

#         return mapped

#     except Exception:
#         logger.warning(
#             "Could not fetch job runs for current viewer",
#             exc_info=True,
#         )

#         return []


# # ---------------------------------------------------------------------------
# # CURRENTLY RUNNING JOBS
# # ---------------------------------------------------------------------------

# def fetch_running_job_count(
#     request: Request,
# ) -> int:
#     """
#     Count currently active Job Runs visible to the current user.
#     """

#     try:
#         viewer_token = _viewer_token(request)

#         # ---------------------------------------------------------------
#         # Viewer path
#         # ---------------------------------------------------------------
#         if viewer_token:
#             data = _viewer_rest_get(
#                 request,
#                 "/api/2.1/jobs/runs/list",
#                 {
#                     "active_only": "true",
#                     "limit": MAX_RUNS,
#                 },
#             )

#             count = len(data.get("runs", []))

#             logger.info(
#                 "Current viewer has %d active job runs",
#                 count,
#             )

#             return count

#         # ---------------------------------------------------------------
#         # Local/default path
#         # ---------------------------------------------------------------

#         sdk = _default_sdk()

#         count = 0

#         for _ in sdk.jobs.list_runs(active_only=True):
#             count += 1

#             if count >= MAX_RUNS:
#                 break

#         logger.info(
#             "Default identity has %d active job runs",
#             count,
#         )

#         return count

#     except Exception:
#         logger.warning(
#             "Could not count active job runs for current viewer",
#             exc_info=True,
#         )

#         return 0


# # ---------------------------------------------------------------------------
# # JOB REGISTRY
# # ---------------------------------------------------------------------------

# def fetch_job_registry(
#     request: Request,
# ) -> dict[int, dict]:
#     """
#     Return a registry of Jobs visible to the current logged-in user.

#     Result:

#         {
#             job_id: {
#                 "name": "...",
#                 "tags": {...}
#             }
#         }
#     """

#     try:
#         viewer_token = _viewer_token(request)

#         registry: dict[int, dict] = {}

#         # ---------------------------------------------------------------
#         # Viewer path — REST API
#         # ---------------------------------------------------------------

#         if viewer_token:
#             page_token: str | None = None

#             while True:
#                 params = {
#                     "limit": 100,
#                 }

#                 if page_token:
#                     params["page_token"] = page_token

#                 data = _viewer_rest_get(
#                     request,
#                     "/api/2.2/jobs/list",
#                     params,
#                 )

#                 for job in data.get("jobs", []):
#                     job_id = job.get("job_id")

#                     if job_id is None:
#                         continue

#                     settings = job.get("settings") or {}

#                     name = (
#                         settings.get("name")
#                         or f"job-{job_id}"
#                     )

#                     tags = settings.get("tags") or {}

#                     registry[int(job_id)] = {
#                         "name": name,
#                         "tags": tags,
#                     }

#                 page_token = data.get("next_page_token")

#                 if not page_token:
#                     break

#             logger.info(
#                 "Fetched %d Jobs visible to current viewer",
#                 len(registry),
#             )

#             return registry

#         # ---------------------------------------------------------------
#         # Local/default path — SDK
#         # ---------------------------------------------------------------

#         sdk = _default_sdk()

#         for job in sdk.jobs.list():
#             settings = job.settings

#             name = (
#                 (settings.name if settings else None)
#                 or f"job-{job.job_id}"
#             )

#             tags = (
#                 (settings.tags if settings else None)
#                 or {}
#             )

#             registry[job.job_id] = {
#                 "name": name,
#                 "tags": tags,
#             }

#         logger.info(
#             "Fetched %d Jobs using default identity",
#             len(registry),
#         )

#         return registry

#     except Exception:
#         logger.warning(
#             "Could not list Jobs for current viewer",
#             exc_info=True,
#         )

#         return {}


# # ---------------------------------------------------------------------------
# # SIMPLE JOB LIST
# # ---------------------------------------------------------------------------

# def fetch_jobs(
#     request: Request,
# ) -> list[dict]:
#     """
#     Return a simple list of Jobs visible to the current logged-in user.

#     Useful for the SentinelOps Jobs page.
#     """

#     try:
#         viewer_token = _viewer_token(request)

#         jobs: list[dict] = []

#         # ---------------------------------------------------------------
#         # Viewer path — REST API
#         # ---------------------------------------------------------------

#         if viewer_token:
#             page_token: str | None = None

#             while True:
#                 params = {
#                     "limit": 100,
#                 }

#                 if page_token:
#                     params["page_token"] = page_token

#                 data = _viewer_rest_get(
#                     request,
#                     "/api/2.2/jobs/list",
#                     params,
#                 )

#                 for job in data.get("jobs", []):
#                     job_id = job.get("job_id")

#                     if job_id is None:
#                         continue

#                     settings = job.get("settings") or {}

#                     name = (
#                         settings.get("name")
#                         or f"job-{job_id}"
#                     )

#                     tags = settings.get("tags") or {}

#                     jobs.append(
#                         {
#                             "job_id": str(job_id),
#                             "name": name,
#                             "tags": tags,
#                         }
#                     )

#                 page_token = data.get("next_page_token")

#                 if not page_token:
#                     break

#             logger.info(
#                 "Fetched %d Jobs for current viewer",
#                 len(jobs),
#             )

#             return jobs

#         # ---------------------------------------------------------------
#         # Local/default path — SDK
#         # ---------------------------------------------------------------

#         sdk = _default_sdk()

#         for job in sdk.jobs.list():
#             settings = job.settings

#             name = (
#                 (settings.name if settings else None)
#                 or f"job-{job.job_id}"
#             )

#             tags = (
#                 (settings.tags if settings else None)
#                 or {}
#             )

#             jobs.append(
#                 {
#                     "job_id": str(job.job_id),
#                     "name": name,
#                     "tags": tags,
#                 }
#             )

#         logger.info(
#             "Fetched %d Jobs using default identity",
#             len(jobs),
#         )

#         return jobs

#     except Exception:
#         logger.warning(
#             "Could not fetch Jobs for current viewer",
#             exc_info=True,
#         )

#         return []


# # ---------------------------------------------------------------------------
# # FORMATTING HELPERS
# # ---------------------------------------------------------------------------

# def job_tag_label(tags: dict) -> str:
#     for key in (
#         "team",
#         "project",
#         "department",
#         "cost_center",
#         "domain",
#     ):
#         if tags.get(key):
#             return tags[key]

#     if tags:
#         return next(iter(tags.values()))

#     return "General"


# def format_timestamp(
#     dt: datetime | None,
# ) -> str:
#     if not dt:
#         return "–"

#     dt = dt.astimezone()

#     hour12 = dt.hour % 12 or 12
#     ampm = "AM" if dt.hour < 12 else "PM"

#     return (
#         f"{dt.strftime('%b')} "
#         f"{dt.day}, "
#         f"{dt.year} "
#         f"{hour12:02d}:{dt.minute:02d} "
#         f"{ampm}"
#     )


# def format_duration(
#     start: datetime | None,
#     end: datetime | None,
# ) -> str:

#     if not start or not end:
#         return "–"

#     seconds = max(
#         0,
#         int((end - start).total_seconds()),
#     )

#     h, rem = divmod(seconds, 3600)
#     m, s = divmod(rem, 60)

#     if h:
#         return f"{h}h {m}m {s}s"

#     if m:
#         return f"{m}m {s}s"

#     return f"{s}s"


# def format_timestamp_ms(
#     ms: int | None,
# ) -> str:

#     if not ms:
#         return "–"

#     return format_timestamp(
#         datetime.fromtimestamp(ms / 1000)
#     )


# def format_duration_ms(
#     start_ms: int | None,
#     end_ms: int | None,
# ) -> str:

#     if not start_ms or not end_ms:
#         return "–"

#     return format_duration(
#         datetime.fromtimestamp(start_ms / 1000),
#         datetime.fromtimestamp(end_ms / 1000),
#     )


# # ---------------------------------------------------------------------------
# # RUN SUMMARIES
# # ---------------------------------------------------------------------------

# def summarize_run_status(
#     runs: list[dict],
# ) -> dict:

#     counts = {
#         "success": 0,
#         "failed": 0,
#         "cancelled": 0,
#     }

#     for r in runs:

#         counts[
#             bucket_result_state(
#                 r["result_state"]
#             )
#         ] += 1

#     return {
#         "total_runs": len(runs),
#         **counts,
#     }


# def build_daily_trend(
#     runs: list[dict],
#     days: int,
# ) -> list[dict]:
#     """
#     One point per day for the last *days* days.
#     Oldest day first.
#     """

#     today = date.today()

#     buckets: dict[date, dict] = {
#         today - timedelta(days=offset): {
#             "success": 0,
#             "failed": 0,
#             "cancelled": 0,
#         }
#         for offset in range(
#             days - 1,
#             -1,
#             -1,
#         )
#     }

#     for r in runs:

#         if not r.get("period_start_time"):
#             continue

#         run_date = r["period_start_time"].date()

#         bucket = buckets.get(run_date)

#         if bucket is not None:

#             bucket[
#                 bucket_result_state(
#                     r["result_state"]
#                 )
#             ] += 1

#     return [
#         {
#             "label": f"{d.strftime('%b')} {d.day}",
#             **counts,
#         }
#         for d, counts in buckets.items()
#     ]

"""
job_service.py — Job runs & registry via system.lakeflow system tables
======================================================================

Purpose:
    Fetch Databricks Jobs and Job Runs for the SentinelOps Jobs page.

Why not the Jobs REST API:

    Databricks Apps user authorization (x-forwarded-access-token) only
    supports a fixed set of OAuth scopes - sql, files, genie,
    model-serving, postgres, vector-search, apps, ai-gateway, plus the
    catalog.* / workspace.workspace SDK scopes. There is NO "jobs"
    scope, so calling /api/2.1/jobs/runs/list with the forwarded viewer
    token always fails with:

        403  Invalid scope, required scopes: jobs

    Calling the same endpoint with the App's service principal works
    only if that SP holds CAN_VIEW on every Job, which we cannot grant.

What we do instead:

    Read run history from the system tables, through the SQL Warehouse
    connection that databricks_client.py already pools:

        system.lakeflow.job_run_timeline   -> runs, states, timings
        system.lakeflow.jobs               -> names, tags, owners

    Access is governed by a single Unity Catalog grant on the schema
    rather than per-job ACLs:

        GRANT USE CATALOG ON CATALOG system TO `<app-service-principal>`;
        GRANT USE SCHEMA  ON SCHEMA system.lakeflow TO `<app-service-principal>`;
        GRANT SELECT      ON SCHEMA system.lakeflow TO `<app-service-principal>`;

Trade-offs you should know about:

    * system.lakeflow is account-wide within the cloud region, so it is
      NOT filtered by per-job permissions. Anyone who can open this app
      sees every job in the workspace. Set DATABRICKS_WORKSPACE_ID to at
      least confine results to this workspace.
    * Rows land with roughly 10-15 minutes of lag, so very recent runs
      may be missing and in-flight runs are approximate.
    * Retention is 365 days.
    * The tables are unavailable in some regions (e.g. asia-south1).

The `request` argument on the public functions is retained so the
existing routers keep working unchanged. It is no longer used.
"""

import json
import logging
import os
import threading
import time
from datetime import date, datetime, timedelta, timezone

from databricks_client import run_query
from viewer_sql import run_query_as_viewer, viewer_id, viewer_token

logger = logging.getLogger("sentinelops.jobs")

MAX_RUNS = 5000

# The Job intelligence page fires five panels at once, and each one now
# costs a SQL Warehouse round-trip instead of a REST call. Against a
# 4-connection pool that queues badly, so identical queries within this
# window are served from memory. System tables lag ~10-15 min anyway, so
# a few seconds of staleness costs nothing real.
CACHE_SECONDS = 20

_cache: dict = {}
_cache_lock = threading.Lock()


def _cached(key: tuple, producer):
    """
    Memoise a query result for CACHE_SECONDS, keyed on its arguments.

    NOTE: results are fetched as the logged-in viewer's token, so two
    viewers can be entitled to different results. The viewer id in the
    cache key prevents serving one user's rows to another.
    """
    now = time.monotonic()

    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]

    value = producer()

    with _cache_lock:
        _cache[key] = (now + CACHE_SECONDS, value)

    return value

# Confine results to this workspace. system.lakeflow spans every workspace
# in the account within the same cloud region, so without this the page can
# show runs from workspaces the viewer never uses.
#
# Databricks Apps injects DATABRICKS_WORKSPACE_ID into every app runtime
# automatically - nothing to configure in app.yaml. It is simply absent in
# local dev, where the filter is skipped. To set it locally, find the id
# with:  SELECT DISTINCT workspace_id FROM system.lakeflow.jobs
WORKSPACE_ID = (os.getenv("DATABRICKS_WORKSPACE_ID") or "").strip()


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


def _as_utc(value) -> datetime | None:
    """
    Normalise a timestamp coming back from the SQL connector to an
    aware UTC datetime.

    The connector may hand back naive datetimes. format_timestamp()
    calls .astimezone(), which would otherwise read a naive UTC value as
    local time and shift every timestamp by the local offset.
    """
    if value is None:
        return None

    if isinstance(value, str):
        # Statement Execution API path already converts TIMESTAMP columns,
        # but a column typed as STRING would arrive raw.
        try:
            text = value.strip().replace(" ", "T")
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            value = datetime.fromisoformat(text)
        except ValueError:
            return None

    if not isinstance(value, datetime):
        return None

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc)


def _as_tag_dict(value) -> dict:
    """
    Normalise a Unity Catalog MAP<STRING, STRING> column to a plain dict.

    Depending on connector version this arrives as a dict, a list of
    key/value pairs, or None.
    """
    if isinstance(value, dict):
        return value

    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}

    if isinstance(value, (list, tuple)):
        try:
            return {k: v for k, v in value}
        except (TypeError, ValueError):
            return {}

    return {}


def _workspace_clause(alias: str = "") -> str:
    """Optional workspace filter, as a SQL fragment."""
    if not WORKSPACE_ID:
        return ""

    prefix = f"{alias}." if alias else ""
    return f"AND {prefix}workspace_id = :workspace_id"


def _workspace_params() -> dict:
    return {"workspace_id": WORKSPACE_ID} if WORKSPACE_ID else {}


def _query(request, statement: str, params: dict) -> list[dict]:
    """
    Run a query as the logged-in viewer when there is one, else as the
    app's service principal.

    The viewer path is the one that matters in production: the app SP has
    no SELECT on system.lakeflow (that needs metastore admin on the
    `system` catalog). The SP path exists for local development, where
    there is no forwarded token and DATABRICKS_TOKEN is your own.
    """
    token = viewer_token(request)

    if token:
        return run_query_as_viewer(token, statement, params)

    return run_query(statement, params)


# ---------------------------------------------------------------------------
# JOBS REST API FALLBACK
# ---------------------------------------------------------------------------

# Reading system.lakeflow needs USE CATALOG / USE SCHEMA / SELECT granted
# by a metastore admin. When the viewer doesn't have those, fall back to
# the Jobs REST API.
#
# SECURITY: these calls use the app's SERVICE PRINCIPAL, not the viewer.
# Databricks Apps user authorization has no Jobs scope, so the forwarded
# viewer token gets 403 from every Jobs endpoint. As a deliberate product
# decision, every viewer sees all jobs/runs the service principal can see,
# regardless of their own job ACLs.
#
# runs/list caps `limit` at 25 per page and every page is its own HTTP
# round-trip, so the fallback is bounded well below MAX_RUNS.
REST_MAX_RUNS = 1000
_REST_PAGE_SIZE = 25


def _with_rest_fallback(what: str, primary, fallback):
    """Run the system-table query; on any failure, use the REST fallback."""
    try:
        return primary()
    except Exception as e:
        first_line = str(e).splitlines()[0][:200] if str(e) else type(e).__name__
        logger.info(
            "system.lakeflow unavailable for %s (%s) - using Jobs REST API",
            what,
            first_line,
        )
        return fallback()


def _ms_to_utc(ms) -> datetime | None:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc) if ms else None


def _rest_job_runs(days: int) -> list[dict]:
    from databricks_client import rest_get_all

    start_from = int(
        (datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000
    )
    runs = rest_get_all(
        "/api/2.1/jobs/runs/list",
        "runs",
        params={
            "completed_only": "true",
            "start_time_from": start_from,
            "limit": _REST_PAGE_SIZE,
        },
        max_pages=REST_MAX_RUNS // _REST_PAGE_SIZE,
        use_sp=True,
    )

    mapped = []
    for run in runs:
        state = run.get("state") or {}
        result_state = state.get("result_state")
        if not result_state:
            continue

        # runs/list reports the termination code under status, not state.
        termination = (run.get("status") or {}).get("termination_details") or {}

        mapped.append(
            {
                "job_id": str(run.get("job_id")),
                "run_id": str(run.get("run_id")),
                "result_state": result_state,
                "period_start_time": _ms_to_utc(run.get("start_time")),
                "period_end_time": _ms_to_utc(run.get("end_time")),
                "termination_code": termination.get("code") or result_state,
            }
        )

    return mapped


def _rest_running_count() -> int:
    from databricks_client import rest_get_all

    runs = rest_get_all(
        "/api/2.1/jobs/runs/list",
        "runs",
        params={"active_only": "true", "limit": _REST_PAGE_SIZE},
        use_sp=True,
    )
    return len(runs)


def _rest_job_registry() -> dict[int, dict]:
    """Unlike system.lakeflow.jobs this has no deleted jobs, so runs of a
    deleted job fall back to the "job-<id>" name."""
    from databricks_client import rest_get_all

    jobs = rest_get_all(
        "/api/2.1/jobs/list", "jobs", params={"limit": 100}, use_sp=True
    )

    return {
        int(job["job_id"]): {
            "name": (job.get("settings") or {}).get("name") or f"job-{job['job_id']}",
            "tags": (job.get("settings") or {}).get("tags") or {},
        }
        for job in jobs
        if job.get("job_id") is not None
    }


# ---------------------------------------------------------------------------
# JOB RUNS
# ---------------------------------------------------------------------------

# job_run_timeline splits a long run into multiple hourly period rows, so
# each run must be collapsed back to a single row. result_state is only
# populated on the terminal period - a NULL means the run is still going,
# which is the system-table equivalent of completed_only=true.
_RUNS_SQL = f"""
SELECT
    job_id,
    run_id,
    MIN(period_start_time)                        AS period_start_time,
    MAX(period_end_time)                          AS period_end_time,
    MAX_BY(result_state,     period_end_time)     AS result_state,
    MAX_BY(termination_code, period_end_time)     AS termination_code
FROM system.lakeflow.job_run_timeline
WHERE period_start_time >= dateadd(DAY, -CAST(:days AS INT), current_timestamp())
    {_workspace_clause()}
GROUP BY job_id, run_id
HAVING MAX_BY(result_state, period_end_time) IS NOT NULL
ORDER BY period_end_time DESC
LIMIT {int(MAX_RUNS)}
"""


def fetch_job_runs(request=None, days: int = 30) -> list[dict]:
    """
    Fetch completed job runs from the last *days* days.

    Returns the same row shape the routers already consume:

        {
            "job_id": str,
            "run_id": str,
            "result_state": str,
            "period_start_time": datetime | None,
            "period_end_time": datetime | None,
            "termination_code": str | None,
        }
    """
    # Legacy call style: the pre-refactor code called fetch_job_runs(30)
    # with no Request. Without this, fetch_job_runs(14) from an unmigrated
    # caller would silently bind 14 to `request` and return a 30-day
    # window instead - wrong data, no error.
    if isinstance(request, int):
        days = request
        request = None  # it was never a Request; don't pass it downstream

    params = {"days": int(days), **_workspace_params()}

    def _from_system_table() -> list[dict]:
        rows = _query(request, _RUNS_SQL, params)
        return [
            {
                "job_id": str(r["job_id"]),
                "run_id": str(r["run_id"]),
                "result_state": r["result_state"],
                "period_start_time": _as_utc(r["period_start_time"]),
                "period_end_time": _as_utc(r["period_end_time"]),
                "termination_code": (
                    r.get("termination_code") or r["result_state"]
                ),
            }
            for r in rows
        ]

    try:
        mapped = _cached(
            (viewer_id(request), "runs", int(days)),
            lambda: _with_rest_fallback(
                "job runs", _from_system_table, lambda: _rest_job_runs(int(days))
            ),
        )

        logger.info(
            "Fetched %d completed job runs (%d day window)",
            len(mapped),
            days,
        )

        return mapped

    except Exception:
        logger.warning(
            "Could not fetch job runs from system.lakeflow or Jobs REST API",
            exc_info=True,
        )

        return []


# ---------------------------------------------------------------------------
# CURRENTLY RUNNING JOBS
# ---------------------------------------------------------------------------

# A run with no result_state on its most recent period has not terminated.
# Two days is a generous ceiling on run length while keeping the scan small.
_RUNNING_SQL = f"""
SELECT COUNT(*) AS running_count
FROM (
    SELECT
        run_id,
        MAX_BY(result_state, period_end_time) AS result_state
    FROM system.lakeflow.job_run_timeline
    WHERE period_start_time >= dateadd(DAY, -2, current_timestamp())
        {_workspace_clause()}
    GROUP BY run_id
)
WHERE result_state IS NULL
"""


def fetch_running_job_count(request=None) -> int:
    """
    Approximate count of in-flight job runs.

    System tables lag live state by roughly 10-15 minutes, so treat this
    as "recently running" rather than a real-time gauge. Surface that in
    the UI if the stat card implies otherwise.
    """
    def _from_system_table() -> int:
        rows = _query(request, _RUNNING_SQL, _workspace_params())
        return int(rows[0]["running_count"]) if rows else 0

    try:
        count = _cached(
            (viewer_id(request), "running"),
            lambda: _with_rest_fallback(
                "running count", _from_system_table, _rest_running_count
            ),
        )

        logger.info("%d job runs currently in flight", count)

        return count

    except Exception:
        logger.warning(
            "Could not count active job runs from system.lakeflow or Jobs REST API",
            exc_info=True,
        )

        return 0


# ---------------------------------------------------------------------------
# JOB REGISTRY
# ---------------------------------------------------------------------------

# system.lakeflow.jobs is an SCD2 table - every edit emits a new row - so
# take the latest row per job. Deleted jobs are kept so historical runs
# still resolve to a name instead of falling back to "job-<id>".
_REGISTRY_SQL = f"""
SELECT job_id, name, tags
FROM (
    SELECT
        job_id,
        name,
        tags,
        ROW_NUMBER() OVER (
            PARTITION BY workspace_id, job_id
            ORDER BY change_time DESC
        ) AS rn
    FROM system.lakeflow.jobs
    WHERE 1 = 1
        {_workspace_clause()}
)
WHERE rn = 1
"""


def fetch_job_registry(request=None) -> dict[int, dict]:
    """
    Return a registry of jobs keyed by int job_id:

        {job_id: {"name": "...", "tags": {...}}}
    """
    def _from_system_table() -> dict[int, dict]:
        rows = _query(request, _REGISTRY_SQL, _workspace_params())
        registry: dict[int, dict] = {}

        for r in rows:
            job_id = r.get("job_id")

            if job_id is None:
                continue

            registry[int(job_id)] = {
                "name": r.get("name") or f"job-{job_id}",
                "tags": _as_tag_dict(r.get("tags")),
            }

        return registry

    try:
        registry = _cached(
            (viewer_id(request), "registry"),
            lambda: _with_rest_fallback(
                "job registry", _from_system_table, _rest_job_registry
            ),
        )

        logger.info("Fetched %d jobs for registry", len(registry))

        return registry

    except Exception:
        logger.warning(
            "Could not fetch job registry from system.lakeflow or Jobs REST API",
            exc_info=True,
        )

        return {}


# ---------------------------------------------------------------------------
# SINGLE RUN DETAIL  (for the RCA drawer)
# ---------------------------------------------------------------------------

_RUN_DETAIL_SQL = f"""
SELECT
    job_id,
    run_id,
    MIN(period_start_time)                        AS period_start_time,
    MAX(period_end_time)                          AS period_end_time,
    MAX_BY(result_state,     period_end_time)     AS result_state,
    MAX_BY(termination_code, period_end_time)     AS termination_code,
    MAX_BY(run_name,         period_end_time)     AS run_name
FROM system.lakeflow.job_run_timeline
WHERE run_id = :run_id
    {_workspace_clause()}
GROUP BY job_id, run_id
"""

# NOTE: verify these column names against your metastore before relying on
# them - in job_task_run_timeline, `run_id` is the TASK run id and
# `job_run_id` is the parent job run:
#     DESCRIBE system.lakeflow.job_task_run_timeline;
_TASK_DETAIL_SQL = f"""
SELECT
    task_key,
    MIN(period_start_time)                        AS period_start_time,
    MAX(period_end_time)                          AS period_end_time,
    MAX_BY(result_state,     period_end_time)     AS result_state,
    MAX_BY(termination_code, period_end_time)     AS termination_code
FROM system.lakeflow.job_task_run_timeline
WHERE job_run_id = :run_id
    {_workspace_clause()}
GROUP BY task_key
"""


def _to_ms(dt: datetime | None) -> int | None:
    dt = _as_utc(dt)
    return int(dt.timestamp() * 1000) if dt else None


def fetch_run_detail(request, run_id: int | str) -> tuple[dict, list[dict]]:
    """
    Load one run plus its failed tasks, in the shape the RCA drawer
    already expects from the Jobs API.

    IMPORTANT LIMITATION: system tables record *which* task failed and
    its termination code. They do NOT carry error messages, exception
    text, or stack traces - that data only exists behind
    /api/2.1/jobs/runs/get, which needs a permission we don't have. So
    `error_trace` is always None and `error` is a coded label rather
    than a real message. Expect thinner RCA output than before.

    Raises LookupError if the run isn't in the system tables, which also
    happens for runs younger than the ~10-15 minute ingestion lag.
    """
    params = {"run_id": str(run_id), **_workspace_params()}

    rows = _query(request, _RUN_DETAIL_SQL, params)

    if not rows:
        raise LookupError(
            f"run {run_id} not found in system.lakeflow.job_run_timeline "
            "(it may be too recent - system tables lag by 10-15 minutes)"
        )

    r = rows[0]
    result_state = r.get("result_state")
    termination_code = r.get("termination_code")

    run = {
        "job_id": int(r["job_id"]),
        "run_id": int(r["run_id"]),
        "run_name": r.get("run_name"),
        "start_time": _to_ms(r.get("period_start_time")),
        "end_time": _to_ms(r.get("period_end_time")),
        "state": {
            "result_state": result_state,
            "life_cycle_state": (
                "TERMINATED" if result_state else "RUNNING"
            ),
            "termination_code": termination_code,
            # Stands in for the Jobs API's state_message, which we can't read.
            "state_message": cause_label(termination_code or result_state),
        },
    }

    try:
        task_rows = _query(request, _TASK_DETAIL_SQL, params)
    except Exception:
        logger.warning(
            "Could not load task detail for run %s", run_id, exc_info=True
        )
        task_rows = []

    failed_tasks = [
        {
            "task_key": t.get("task_key"),
            "result_state": t.get("result_state"),
            "termination_code": t.get("termination_code"),
            "error": (
                f"Task '{t.get('task_key')}' ended as "
                f"{t.get('result_state')} — "
                f"{cause_label(t.get('termination_code'))}"
            ),
            "error_trace": None,
            "start_time": _to_ms(t.get("period_start_time")),
            "end_time": _to_ms(t.get("period_end_time")),
        }
        for t in task_rows
        if t.get("result_state")
        and bucket_result_state(t["result_state"]) == "failed"
    ]

    return run, failed_tasks


# ---------------------------------------------------------------------------
# SIMPLE JOB LIST
# ---------------------------------------------------------------------------

def fetch_jobs(request=None) -> list[dict]:
    """Fetch jobs via REST API as the service principal (no system.lakeflow
    grants needed). See JOBS REST API FALLBACK: viewer tokens get 403."""
    from databricks_client import rest_get_all

    try:
        jobs = rest_get_all("/api/2.1/jobs/list", "jobs", use_sp=True)
        
        return [
            {
                "job_id": str(job["job_id"]),
                "name": job.get("settings", {}).get("name", f"job-{job['job_id']}"),
                "tags": job.get("settings", {}).get("tags", {}),
            }
            for job in jobs
        ]
    except Exception:
        logger.warning("Could not fetch jobs via REST API", exc_info=True)
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


def format_timestamp(dt: datetime | None) -> str:
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


def format_timestamp_ms(ms: int | None) -> str:
    if not ms:
        return "–"

    return format_timestamp(
        datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    )


def format_duration_ms(
    start_ms: int | None,
    end_ms: int | None,
) -> str:

    if not start_ms or not end_ms:
        return "–"

    return format_duration(
        datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc),
        datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc),
    )


# ---------------------------------------------------------------------------
# RUN SUMMARIES
# ---------------------------------------------------------------------------

def summarize_run_status(runs: list[dict]) -> dict:

    counts = {
        "success": 0,
        "failed": 0,
        "cancelled": 0,
    }

    for r in runs:
        counts[bucket_result_state(r["result_state"])] += 1

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
        for offset in range(days - 1, -1, -1)
    }

    for r in runs:

        if not r.get("period_start_time"):
            continue

        run_date = r["period_start_time"].astimezone().date()

        bucket = buckets.get(run_date)

        if bucket is not None:
            bucket[bucket_result_state(r["result_state"])] += 1

    return [
        {
            "label": f"{d.strftime('%b')} {d.day}",
            **counts,
        }
        for d, counts in buckets.items()
    ]