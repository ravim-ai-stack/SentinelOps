# """
# Root cause analysis for a single failed job run — fetches the run's
# per-task error output from the Jobs REST API and asks a Databricks model
# endpoint (MODEL_NAME) to explain the failure and recommend a fix, via the
# SQL warehouse's ai_query() function.

# Ported from the standalone Sentinel_Ops polling prototype (app/rca.py +
# app/poller.py), adapted to be called on demand for one run at a time
# (from the Job Intelligence drawer's "View details") instead of on a
# background poll loop, and to this app's plain function + REST/SQL helper
# style instead of a client class.
# """

# import json
# import logging
# import os
# import re
# from datetime import datetime, timezone

# from databricks_client import rest_get, run_query

# logger = logging.getLogger("sentinelops.rca")

# MODEL_NAME = os.environ.get("MODEL_NAME", "").strip()

# SYSTEM_PROMPT = (
#     "You are a senior data platform engineer performing root cause analysis on a failed "
#     "Databricks job. You will be given job metadata, run metadata, error messages, and "
#     "stack traces. Respond with ONLY a JSON object with exactly three keys: \"root_cause\" "
#     "(a concise plain-text explanation of why the job failed), \"recommendation\" "
#     "(concrete, actionable steps to fix it, as a single plain-text string, using \"\\n\" "
#     "between steps if there are multiple), and \"confidence\" (an integer from 0 to 100 "
#     "estimating how confident you are in this root cause given the evidence). Do not "
#     "include any text outside the JSON object."
# )

# _MAX_FIELD_CHARS = 3000
# _JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)
# _ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


# def _truncate(text: str | None, limit: int = _MAX_FIELD_CHARS) -> str:
#     text = text or ""
#     return text if len(text) <= limit else text[:limit] + "... [truncated]"


# def _strip_ansi(text: str | None) -> str | None:
#     return _ANSI_RE.sub("", text) if text else text


# def _task_output(task_key: str, run_id: int) -> dict:
#     try:
#         output = rest_get("/api/2.1/jobs/runs/get-output", {"run_id": run_id})
#         return {
#             "task_key": task_key,
#             "error": _strip_ansi(output.get("error")),
#             "error_trace": _strip_ansi(output.get("error_trace")),
#             "logs": _strip_ansi(output.get("logs")),
#         }
#     except Exception as exc:
#         return {"task_key": task_key, "error": str(exc), "error_trace": None, "logs": None}


# def fetch_failed_tasks(run_id: int) -> tuple[dict, list[dict]]:
#     """Run metadata + error output for each of its failed tasks (or the
#     run itself, for a single-task job)."""
#     run = rest_get("/api/2.1/jobs/runs/get", {"run_id": run_id})
#     state = run.get("state") or {}
#     tasks = run.get("tasks") or []
#     failed: list[dict] = []
#     if tasks:
#         for task in tasks:
#             task_state = task.get("state") or {}
#             if task_state.get("result_state") == "FAILED":
#                 failed.append(_task_output(task.get("task_key", "task"), task["run_id"]))
#     elif state.get("result_state") == "FAILED":
#         failed.append(_task_output(run.get("run_name") or "task", run["run_id"]))
#     return run, failed


# def _build_prompt(job_name: str, job_id: int, tags: dict, run: dict, failed_tasks: list[dict]) -> str:
#     state = run.get("state") or {}
#     start, end = run.get("start_time"), run.get("end_time")
#     duration = round((end - start) / 1000, 2) if start and end else None
#     lines = [
#         f"Job name: {job_name}",
#         f"Job ID: {job_id}",
#         f"Tags: {tags or {}}",
#         f"Run ID: {run.get('run_id')}",
#         f"Run state message: {state.get('state_message') or 'N/A'}",
#         f"Duration: {duration} seconds",
#         "",
#         "Failed tasks:",
#     ]
#     for task in failed_tasks:
#         lines.append(f"- Task: {task['task_key']}")
#         lines.append(f"  Error: {_truncate(task.get('error') or 'N/A')}")
#         if task.get("error_trace"):
#             lines.append(f"  Stack trace: {_truncate(task['error_trace'])}")
#         if task.get("logs"):
#             lines.append(f"  Logs: {_truncate(task['logs'])}")
#     return "\n".join(lines)


# def _parse_response(content: str) -> dict:
#     cleaned = _JSON_FENCE_RE.sub("", content.strip())
#     try:
#         parsed = json.loads(cleaned)
#     except (json.JSONDecodeError, AttributeError):
#         return {"root_cause": cleaned, "recommendation": "", "confidence": 70}

#     try:
#         confidence = max(0, min(100, int(parsed.get("confidence"))))
#     except (TypeError, ValueError):
#         confidence = 70

#     return {
#         "root_cause": str(parsed.get("root_cause") or "").strip() or "Model returned no root cause.",
#         "recommendation": str(parsed.get("recommendation") or "").strip(),
#         "confidence": confidence,
#     }


# def generate_rca(job_name: str, job_id: int, tags: dict, run: dict, failed_tasks: list[dict]) -> dict:
#     """Calls the MODEL_NAME endpoint via the SQL warehouse's ai_query()
#     function for a root cause + fix. Raises if no model is configured or
#     the call/parse fails — callers fall back to the run's raw state
#     message/error text instead."""
#     if not MODEL_NAME:
#         raise RuntimeError("MODEL_NAME is not configured")

#     full_prompt = f"{SYSTEM_PROMPT}\n\n{_build_prompt(job_name, job_id, tags, run, failed_tasks)}"
#     sql = f"""
#         SELECT ai_query(
#             '{MODEL_NAME}',
#             :full_prompt,
#             modelParameters => named_struct('max_tokens', 600, 'temperature', 0.2)
#         ) AS response
#     """
#     rows = run_query(sql, {"full_prompt": full_prompt})
#     content = rows[0]["response"]
#     parsed = _parse_response(content)
#     parsed["generated_at"] = datetime.now(timezone.utc).isoformat()
#     parsed["model"] = MODEL_NAME
#     return parsed


"""
Root cause analysis for a single failed job run.

Run and task detail now arrive from job_service.fetch_run_detail(), which
reads system.lakeflow, so this module no longer calls the Jobs REST API at
all. The AI call still goes through the SQL Warehouse's ai_query().

WHAT CHANGED AND WHY IT MATTERS:

    The old prompt was built from /api/2.1/jobs/runs/get-output, which
    returned the task's actual error message, stack trace and driver
    logs. System tables carry none of that - only which task failed and
    a coded termination reason.

    So the model is now reasoning from a failure CATEGORY, not evidence.
    "DRIVER_ERROR on task transform_silver, 47 minutes" supports a
    reasonable hypothesis; it does not support a specific diagnosis. The
    prompt below says so explicitly, and confidence is clamped to
    MAX_CONFIDENCE_WITHOUT_TRACE when no trace is present, because a
    model handed thin evidence will still happily report 90.

    If someone later restores a source of real error text, populate
    `error_trace` / `logs` on the task dicts and both the prompt and the
    confidence clamp pick it up automatically - no other changes needed.
"""

import json
import logging
import os
import re
from datetime import datetime, timezone

from databricks_client import run_query

logger = logging.getLogger("sentinelops.rca")

MODEL_NAME = os.environ.get("MODEL_NAME", "").strip()

# A root cause inferred from a termination code alone is a hypothesis.
# Don't let the drawer present one as near-certainty.
MAX_CONFIDENCE_WITHOUT_TRACE = 55

SYSTEM_PROMPT = (
    "You are a senior data platform engineer performing root cause "
    "analysis on a failed Databricks job.\n\n"
    "IMPORTANT - about your evidence: you are given job metadata, run "
    "metadata, the failed task names and their Databricks termination "
    "codes. Stack traces, exception messages and driver logs are NOT "
    "available to you unless explicitly included below. Do not invent "
    "specific error messages, line numbers, table names, column names "
    "or file paths that do not appear in the input. Where the "
    "termination code supports more than one explanation, say so and "
    "name the most likely ones rather than picking one arbitrarily. "
    "Set \"confidence\" to reflect how thin the evidence actually is - "
    "a termination code alone rarely justifies more than 50.\n\n"
    "Respond with ONLY a JSON object with exactly three keys: "
    "\"root_cause\" (a concise plain-text explanation of why the job "
    "likely failed), \"recommendation\" (concrete, actionable steps to "
    "diagnose and fix it, as a single plain-text string, using \"\\n\" "
    "between steps if there are multiple), and \"confidence\" (an "
    "integer from 0 to 100). Do not include any text outside the JSON "
    "object."
)

_MAX_FIELD_CHARS = 3000

_JSON_FENCE_RE = re.compile(
    r"^```(?:json)?\s*|\s*```$",
    re.IGNORECASE | re.MULTILINE,
)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _truncate(text: str | None, limit: int = _MAX_FIELD_CHARS) -> str:
    text = text or ""

    return text if len(text) <= limit else text[:limit] + "... [truncated]"


def _strip_ansi(text: str | None) -> str | None:
    return _ANSI_RE.sub("", text) if text else text


def _has_real_evidence(failed_tasks: list[dict]) -> bool:
    """True if any task carries an actual trace or log, not just a code."""
    return any(
        task.get("error_trace") or task.get("logs")
        for task in failed_tasks
    )


def _build_prompt(
    job_name: str,
    job_id: int,
    tags: dict,
    run: dict,
    failed_tasks: list[dict],
) -> str:
    state = run.get("state") or {}

    start = run.get("start_time")
    end = run.get("end_time")

    duration = round((end - start) / 1000, 2) if start and end else None

    lines = [
        f"Job name: {job_name}",
        f"Job ID: {job_id}",
        f"Tags: {tags or {}}",
        f"Run ID: {run.get('run_id')}",
        f"Run result state: {state.get('result_state') or 'N/A'}",
        f"Run termination code: {state.get('termination_code') or 'N/A'}",
        f"Duration: {duration} seconds",
        "",
        "Failed tasks:",
    ]

    if not failed_tasks:
        lines.append(
            "- (none reported individually; the run itself terminated "
            "in the state above)"
        )

    for task in failed_tasks:
        lines.append(f"- Task: {task.get('task_key')}")
        lines.append(f"  Result state: {task.get('result_state') or 'N/A'}")
        lines.append(
            f"  Termination code: {task.get('termination_code') or 'N/A'}"
        )

        if task.get("error_trace"):
            lines.append(
                f"  Stack trace: {_truncate(_strip_ansi(task['error_trace']))}"
            )

        if task.get("logs"):
            lines.append(f"  Logs: {_truncate(_strip_ansi(task['logs']))}")

    if not _has_real_evidence(failed_tasks):
        lines += [
            "",
            "NOTE: No stack traces, exception text or logs are available "
            "for this run. Base your analysis on the termination codes "
            "and task names above, state clearly that the diagnosis is "
            "provisional, and make the recommendation focus on what the "
            "engineer should check in the Databricks Jobs UI to confirm "
            "or rule it out.",
        ]

    return "\n".join(lines)


def _parse_response(content: str, evidence_is_thin: bool = False) -> dict:
    cleaned = _JSON_FENCE_RE.sub("", content.strip())

    try:
        parsed = json.loads(cleaned)

    except (json.JSONDecodeError, AttributeError, TypeError):
        return {
            "root_cause": cleaned,
            "recommendation": "",
            "confidence": (
                min(70, MAX_CONFIDENCE_WITHOUT_TRACE)
                if evidence_is_thin
                else 70
            ),
        }

    try:
        confidence = max(0, min(100, int(parsed.get("confidence"))))
    except (TypeError, ValueError):
        confidence = 70

    if evidence_is_thin:
        confidence = min(confidence, MAX_CONFIDENCE_WITHOUT_TRACE)

    return {
        "root_cause": (
            str(parsed.get("root_cause") or "").strip()
            or "Model returned no root cause."
        ),
        "recommendation": str(parsed.get("recommendation") or "").strip(),
        "confidence": confidence,
    }


def generate_rca(
    job_name: str,
    job_id: int,
    tags: dict,
    run: dict,
    failed_tasks: list[dict],
) -> dict:
    """
    Call the configured model endpoint through the SQL Warehouse's
    ai_query() function.

    Raises if no model is configured or the call/parse fails - callers
    fall back to the run's own failure detail instead.
    """
    if not MODEL_NAME:
        raise RuntimeError("MODEL_NAME is not configured")

    evidence_is_thin = not _has_real_evidence(failed_tasks)

    full_prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        f"{_build_prompt(job_name, job_id, tags, run, failed_tasks)}"
    )

    sql = f"""
        SELECT ai_query(
            '{MODEL_NAME}',
            :full_prompt,
            modelParameters => named_struct(
                'max_tokens', 600,
                'temperature', 0.2
            )
        ) AS response
    """

    rows = run_query(sql, {"full_prompt": full_prompt})

    content = rows[0]["response"]

    parsed = _parse_response(content, evidence_is_thin)

    parsed["generated_at"] = datetime.now(timezone.utc).isoformat()
    parsed["model"] = MODEL_NAME
    parsed["evidence"] = "codes_only" if evidence_is_thin else "full"

    if evidence_is_thin:
        logger.info(
            "RCA for job %s generated from termination codes only "
            "(no trace available); confidence capped at %d",
            job_id,
            MAX_CONFIDENCE_WITHOUT_TRACE,
        )

    return parsed