"""
viewer_sql.py — run SQL as the logged-in app user.

WHY THIS EXISTS

    system.lakeflow is readable by our users but NOT by the app's
    service principal (granting the SP requires metastore admin on the
    `system` catalog, which we don't have). So job queries run with the
    viewer's own forwarded token instead.

    That token is covered by the `sql` OAuth scope, which Databricks Apps
    does support - unlike `jobs`, which does not exist and is why the
    Jobs REST API was unreachable in the first place.

WHY THE STATEMENT EXECUTION API AND NOT databricks-sql-connector

    databricks_client.run_query() pools connections built from the app's
    service-principal credentials. Those connections cannot carry a
    per-user identity, and opening a fresh connector session per viewer
    per request reintroduces exactly the session-storm problem that the
    pool in databricks_client.py exists to prevent (see its POOL_SIZE
    comment - a single page load fans out to several panels at once).

    The Statement Execution API is plain stateless HTTP: no session, no
    pool, no warm-up. One request per query, authenticated with whatever
    bearer token we hand it.

RETURN SHAPE

    run_query_as_viewer() returns list[dict] - the same shape as
    databricks_client.run_query() - so callers can use either one.

    Note that this API returns every value as a JSON string, so values
    are converted back to Python types using the result manifest. That
    conversion is this module's main job.
"""

import json
import logging
import time
from datetime import datetime, timezone

import requests

from databricks_client import DATABRICKS_WAREHOUSE_ID, REST_BASE_URL

logger = logging.getLogger("sentinelops.viewer_sql")

VIEWER_TOKEN_HEADER = "x-forwarded-access-token"

# Identity headers, in preference order, used only to key the result cache
# per viewer. Never used for authentication.
VIEWER_ID_HEADERS = (
    "x-forwarded-preferred-username",
    "x-forwarded-email",
    "x-forwarded-user",
)

# The API accepts "0s" or 5-50s. We wait inline for this long, then poll.
WAIT_TIMEOUT = "30s"
POLL_INTERVAL_SECONDS = 1.0
MAX_POLL_SECONDS = 120
REQUEST_TIMEOUT = 60


def viewer_token(request) -> str | None:
    """The forwarded access token for the logged-in browser user."""
    headers = getattr(request, "headers", None)

    if headers is None:
        return None

    return headers.get(VIEWER_TOKEN_HEADER)


def viewer_id(request) -> str:
    """
    A stable-per-user string for cache keys.

    IMPORTANT: every cache of viewer-scoped results must include this.
    Two users can be entitled to different rows, so a cache keyed only on
    the query would serve one user's results to another.
    """
    headers = getattr(request, "headers", None)

    if headers is None:
        return "local"

    for header in VIEWER_ID_HEADERS:
        value = headers.get(header)
        if value:
            return value

    return "unknown"


# ---------------------------------------------------------------------------
# VALUE CONVERSION
# ---------------------------------------------------------------------------

_TIMESTAMP_FORMATS = (
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
)

_INT_TYPES = {"INT", "LONG", "SHORT", "BYTE"}
_FLOAT_TYPES = {"FLOAT", "DOUBLE", "DECIMAL"}
_JSON_TYPES = {"MAP", "ARRAY", "STRUCT"}


def _parse_timestamp(value: str) -> datetime | None:
    text = value.strip()

    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = None

        for fmt in _TIMESTAMP_FORMATS:
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue

    if parsed is None:
        logger.debug("Could not parse timestamp %r", value)
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def _convert(value, type_name: str):
    if value is None:
        return None

    upper = (type_name or "").upper()

    try:
        if upper in ("TIMESTAMP", "TIMESTAMP_NTZ"):
            return _parse_timestamp(value)

        if upper == "DATE":
            return datetime.strptime(value, "%Y-%m-%d").date()

        if upper in _INT_TYPES:
            return int(value)

        if upper in _FLOAT_TYPES:
            return float(value)

        if upper == "BOOLEAN":
            return value.lower() == "true"

        if upper in _JSON_TYPES:
            return json.loads(value)

    except (ValueError, TypeError, json.JSONDecodeError):
        # Fall through to the raw string rather than losing the row.
        logger.debug("Could not convert %r as %s", value, upper)

    return value


def _rows_from_response(body: dict) -> list[dict]:
    manifest = body.get("manifest") or {}
    schema = manifest.get("schema") or {}
    columns = schema.get("columns") or []

    names = [c.get("name") for c in columns]
    types = [c.get("type_name") for c in columns]

    data = (body.get("result") or {}).get("data_array") or []

    return [
        {
            name: _convert(value, type_name)
            for name, type_name, value in zip(names, types, row)
        }
        for row in data
    ]


# ---------------------------------------------------------------------------
# EXECUTION
# ---------------------------------------------------------------------------

def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _raise_on_failure(status: dict) -> None:
    state = status.get("state")

    if state in ("FAILED", "CANCELED", "CLOSED"):
        error = status.get("error") or {}
        raise RuntimeError(
            f"Statement {state}: "
            f"{error.get('message') or 'no error message returned'}"
        )


def run_query_as_viewer(
    token: str,
    statement: str,
    params: dict | None = None,
) -> list[dict]:
    """
    Execute a SQL statement with the viewer's token and return list[dict].

    `params` keys map to :named markers in the statement. Every value is
    sent as a string; Databricks casts it per the query's own CAST calls,
    which is why the SQL in job_service.py casts explicitly.
    """
    payload = {
        "warehouse_id": DATABRICKS_WAREHOUSE_ID,
        "statement": statement,
        "wait_timeout": WAIT_TIMEOUT,
        "on_wait_timeout": "CONTINUE",
        "format": "JSON_ARRAY",
        "disposition": "INLINE",
    }

    if params:
        payload["parameters"] = [
            {"name": name, "value": str(value)}
            for name, value in params.items()
        ]

    response = requests.post(
        f"{REST_BASE_URL}/api/2.0/sql/statements",
        headers=_headers(token),
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )

    if response.status_code >= 400:
        logger.error(
            "Statement Execution API failed: status=%s body=%s",
            response.status_code,
            response.text[:2000],
        )

    response.raise_for_status()
    body = response.json()

    status = body.get("status") or {}
    _raise_on_failure(status)

    # Long queries return PENDING/RUNNING after wait_timeout; poll them out.
    statement_id = body.get("statement_id")
    waited = 0.0

    while status.get("state") in ("PENDING", "RUNNING"):
        if waited >= MAX_POLL_SECONDS:
            raise TimeoutError(
                f"Statement {statement_id} still running after "
                f"{MAX_POLL_SECONDS}s"
            )

        time.sleep(POLL_INTERVAL_SECONDS)
        waited += POLL_INTERVAL_SECONDS

        poll = requests.get(
            f"{REST_BASE_URL}/api/2.0/sql/statements/{statement_id}",
            headers=_headers(token),
            timeout=REQUEST_TIMEOUT,
        )
        poll.raise_for_status()
        body = poll.json()
        status = body.get("status") or {}
        _raise_on_failure(status)

    if status.get("state") != "SUCCEEDED":
        raise RuntimeError(f"Unexpected statement state: {status.get('state')}")

    return _rows_from_response(body)