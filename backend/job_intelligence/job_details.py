# """Job intelligence page — job details drawer (Overview / Root cause /
# Remediation).

# Live — on first request for a given run_id, fetches the run and its
# failed tasks' error output from the Jobs REST API (rca_service.py) and
# asks the MODEL_NAME serving endpoint to explain the failure and
# recommend a fix. If no model is configured, or the call fails, falls
# back to the run's own state message/task error text instead of an AI
# summary. The result is cached per run_id (rca_store.py) so reopening the
# same run doesn't re-run the model or refetch the run.
# """

# import logging

# from fastapi import APIRouter

# import rca_service
# from job_intelligence import rca_store
# from job_service import fetch_job_registry, format_duration_ms, format_timestamp_ms, job_tag_label

# logger = logging.getLogger("sentinelops.jobs")

# router = APIRouter()


# def _fallback_rca(run: dict, failed_tasks: list[dict]) -> dict:
#     state = run.get("state") or {}
#     task_errors = [t.get("error") for t in failed_tasks if t.get("error")]
#     summary = state.get("state_message") or (task_errors[0] if task_errors else None) \
#         or "No error detail was returned for this run."
#     return {
#         "root_cause": summary,
#         "recommendation": "Open this run in the Databricks Jobs UI for full logs, then rerun after addressing the error above.",
#         "confidence": 40,
#     }


# def _stack_trace_lines(failed_tasks: list[dict]) -> list[str]:
#     lines: list[str] = []
#     for task in failed_tasks:
#         if task.get("error_trace"):
#             lines.extend(task["error_trace"].splitlines())
#         elif task.get("error"):
#             lines.append(task["error"])
#     return lines[:60] or ["No stack trace available for this run."]


# def _recommended_actions(recommendation: str) -> list[dict]:
#     steps = [line.strip(" \t-•") for line in (recommendation or "").splitlines() if line.strip()]
#     return [{"title": step, "description": ""} for step in steps]


# def _status_label(state: dict) -> str:
#     raw = state.get("result_state") or state.get("life_cycle_state") or "Unknown"
#     return raw.replace("_", " ").title()


# @router.get("/api/jobs/{run_id}/details")
# def job_details(run_id: str):
#     cached = rca_store.get_cached(run_id)
#     if cached is None:
#         try:
#             run, failed_tasks = rca_service.fetch_failed_tasks(int(run_id))
#         except Exception:
#             logger.warning("Could not load run %s from Databricks", run_id, exc_info=True)
#             return {
#                 "overview": {
#                     "job_name": "Unknown", "status": "Unknown", "tag": "General",
#                     "failed_at": "–", "run_id": run_id, "duration": "–",
#                 },
#                 "root_cause": {"summary": "Could not load this run from Databricks.", "confidence": 0},
#                 "stack_trace": [],
#                 "recommended_actions": [],
#             }

#         registry = fetch_job_registry()
#         job_id = run.get("job_id")
#         info = registry.get(job_id, {})
#         state = run.get("state") or {}

#         if state.get("result_state") != "FAILED" and not failed_tasks:
#             # This run didn't fail — nothing to analyze, and asking the
#             # model for a "root cause" here would just invite a made-up
#             # one. Not cached in rca_store: it isn't a real RCA, and
#             # caching it would inflate the "RCA generated" stat.
#             cached = {
#                 "run": run, "failed_tasks": failed_tasks,
#                 "rca": {
#                     "root_cause": "This run completed without failure — no root cause analysis needed.",
#                     "recommendation": "",
#                     "confidence": 100,
#                 },
#             }
#         else:
#             try:
#                 rca = rca_service.generate_rca(
#                     info.get("name", f"job-{job_id}"), job_id, info.get("tags"), run, failed_tasks
#                 )
#             except Exception:
#                 logger.info("No AI root cause for run %s (model unavailable) — using run's own error text", run_id, exc_info=True)
#                 rca = _fallback_rca(run, failed_tasks)

#             cached = {"run": run, "failed_tasks": failed_tasks, "rca": rca}
#             rca_store.store(run_id, cached)

#     run, failed_tasks, rca = cached["run"], cached["failed_tasks"], cached["rca"]
#     registry = fetch_job_registry()
#     info = registry.get(run.get("job_id"), {})
#     state = run.get("state") or {}

#     overview = {
#         "job_name": info.get("name", f"job-{run.get('job_id')}"),
#         "status": _status_label(state),
#         "tag": job_tag_label(info.get("tags") or {}),
#         "failed_at": format_timestamp_ms(run.get("end_time") or run.get("start_time")),
#         "run_id": str(run.get("run_id", run_id)),
#         "duration": format_duration_ms(run.get("start_time"), run.get("end_time")),
#     }

#     return {
#         "overview": overview,
#         "root_cause": {"summary": rca["root_cause"], "confidence": rca.get("confidence", 0)},
#         "stack_trace": _stack_trace_lines(failed_tasks),
#         "recommended_actions": _recommended_actions(rca.get("recommendation", "")),
#     }

"""Job intelligence page — job details drawer (Overview / Root cause /
Remediation).

Run and task detail come from system.lakeflow via
job_service.fetch_run_detail(), not the Jobs REST API - there is no
`jobs` OAuth scope for Databricks Apps user authorization. The queries
run with the logged-in viewer's own token (the `sql` scope), because the
app's service principal has no SELECT on system.lakeflow, so the Request
is threaded through to job_service.

Consequence for RCA quality: system tables record which task failed and
its termination code, but carry no error messages or stack traces. The
model therefore gets a coded failure label instead of an exception, so
expect broader, less specific root causes. The stack trace panel renders
an explanatory note rather than pretending to have data.

Results are cached per run_id (rca_store.py) so reopening a run doesn't
re-run the model or re-query the warehouse.
"""

import logging

from fastapi import APIRouter, Request

import rca_service
from job_intelligence import rca_store
from job_service import (
    fetch_job_registry,
    fetch_run_detail,
    format_duration_ms,
    format_timestamp_ms,
    job_tag_label,
)

logger = logging.getLogger("sentinelops.jobs")

router = APIRouter()

_NO_TRACE_NOTE = (
    "Stack traces aren't available — this app reads run history from "
    "Unity Catalog system tables, which record failure codes but not "
    "error output. Open the run in the Databricks Jobs UI for full logs."
)


def _fallback_rca(run: dict, failed_tasks: list[dict]) -> dict:
    """Used when the serving endpoint is unavailable."""
    state = run.get("state") or {}

    task_errors = [t.get("error") for t in failed_tasks if t.get("error")]

    summary = (
        (task_errors[0] if task_errors else None)
        or state.get("state_message")
        or "No error detail was returned for this run."
    )

    return {
        "root_cause": summary,
        "recommendation": (
            "Open this run in the Databricks Jobs UI for full logs, "
            "then rerun after addressing the error above."
        ),
        "confidence": 40,
    }


def _stack_trace_lines(failed_tasks: list[dict]) -> list[str]:
    """
    System tables carry no trace text, so this lists the failed tasks and
    their termination codes. error_trace is kept in the loop so the panel
    starts showing real traces automatically if a richer source is wired
    in later.
    """
    lines: list[str] = []

    for task in failed_tasks:
        if task.get("error_trace"):
            lines.extend(task["error_trace"].splitlines())
        elif task.get("error"):
            lines.append(task["error"])

    if not lines:
        return [_NO_TRACE_NOTE]

    return lines[:60] + [""] + [_NO_TRACE_NOTE]


def _recommended_actions(recommendation: str) -> list[dict]:
    steps = [
        line.strip(" \t-•")
        for line in (recommendation or "").splitlines()
        if line.strip()
    ]

    return [{"title": step, "description": ""} for step in steps]


def _status_label(state: dict) -> str:
    raw = (
        state.get("result_state")
        or state.get("life_cycle_state")
        or "Unknown"
    )

    return raw.replace("_", " ").title()


def _empty_response(run_id: str, message: str) -> dict:
    return {
        "overview": {
            "job_name": "Unknown",
            "status": "Unknown",
            "tag": "General",
            "failed_at": "–",
            "run_id": run_id,
            "duration": "–",
        },
        "root_cause": {"summary": message, "confidence": 0},
        "stack_trace": [],
        "recommended_actions": [],
    }


@router.get("/api/jobs/{run_id}/details")
def job_details(request: Request, run_id: str):
    cached = rca_store.get_cached(run_id)

    if cached is None:
        try:
            run, failed_tasks = fetch_run_detail(request, run_id)

        except LookupError as exc:
            # Most often the ~10-15 min system-table ingestion lag.
            logger.info("Run %s not in system tables yet: %s", run_id, exc)
            return _empty_response(
                run_id,
                "This run isn't available yet. Run history is read from "
                "system tables, which lag live runs by 10-15 minutes.",
            )

        except Exception:
            logger.warning(
                "Could not load run %s from system tables",
                run_id,
                exc_info=True,
            )
            return _empty_response(
                run_id, "Could not load this run from Databricks."
            )

        registry = fetch_job_registry(request)
        job_id = run.get("job_id")
        info = registry.get(job_id, {})
        state = run.get("state") or {}

        if state.get("result_state") != "FAILED" and not failed_tasks:
            # Nothing to analyze. Asking the model for a "root cause"
            # here would just invite a made-up one. Deliberately not
            # stored in rca_store: it isn't a real RCA, and caching it
            # would inflate the "RCA generated" stat card.
            cached = {
                "run": run,
                "failed_tasks": failed_tasks,
                "rca": {
                    "root_cause": (
                        "This run completed without failure — "
                        "no root cause analysis needed."
                    ),
                    "recommendation": "",
                    "confidence": 100,
                },
            }

        else:
            try:
                rca = rca_service.generate_rca(
                    info.get("name", f"job-{job_id}"),
                    job_id,
                    info.get("tags"),
                    run,
                    failed_tasks,
                )

            except Exception:
                logger.info(
                    "No AI root cause for run %s (model unavailable) — "
                    "using the run's own failure detail",
                    run_id,
                    exc_info=True,
                )
                rca = _fallback_rca(run, failed_tasks)

            cached = {
                "run": run,
                "failed_tasks": failed_tasks,
                "rca": rca,
            }

            rca_store.store(run_id, cached)

    run = cached["run"]
    failed_tasks = cached["failed_tasks"]
    rca = cached["rca"]

    registry = fetch_job_registry(request)
    info = registry.get(run.get("job_id"), {})
    state = run.get("state") or {}

    overview = {
        "job_name": info.get("name", f"job-{run.get('job_id')}"),
        "status": _status_label(state),
        "tag": job_tag_label(info.get("tags") or {}),
        "failed_at": format_timestamp_ms(
            run.get("end_time") or run.get("start_time")
        ),
        "run_id": str(run.get("run_id", run_id)),
        "duration": format_duration_ms(
            run.get("start_time"), run.get("end_time")
        ),
    }

    return {
        "overview": overview,
        "root_cause": {
            "summary": rca["root_cause"],
            "confidence": rca.get("confidence", 0),
        },
        "stack_trace": _stack_trace_lines(failed_tasks),
        "recommended_actions": _recommended_actions(
            rca.get("recommendation", "")
        ),
    }