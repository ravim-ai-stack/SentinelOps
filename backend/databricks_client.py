"""
Connection layer to Databricks: Unity Catalog metadata (system.
information_schema) through a SQL Warehouse, plus REST/SCIM calls.

Auth picks one of two identities per call, resolved through
request_context (see that module):
  - a forwarded viewer token (request_context.user_token) - present when
    deployed as a Databricks App with User Authorization enabled, forwarded
    by the platform per-request as the logged-in viewer's own OAuth access
    token. Every query then runs, and is scoped by Unity Catalog, as that
    viewer - not the app's own identity - so each teammate only ever sees
    what they've personally been granted. This token is a scoped OAuth
    token meant for REST API calls (matching app.yaml's api_scopes) - not
    usable to open a Thrift-protocol SQL Warehouse session - so run_query
    executes it via the SQL Statement Execution REST API instead of
    databricks-sql-connector; see run_query/_run_query_as_viewer below.
  - no viewer token (local dev, or User Authorization not enabled): falls
    back to databricks-sdk's Config(), which resolves to DATABRICKS_HOST /
    DATABRICKS_TOKEN from backend/.env locally, or the app's own
    service-principal OAuth credentials when deployed (auto-injected by the
    platform - no .env, no secret ever committed). This path keeps using
    databricks-sql-connector's pooled Thrift connections, as before.

Browser-level login to the app itself (workspace SSO) is handled entirely
by the platform - not this file.
"""

import os
import queue
import time

import requests
from databricks import sql
from databricks.sdk.core import Config
from dotenv import load_dotenv

import request_context

load_dotenv()

cfg = Config()

DATABRICKS_WAREHOUSE_ID = os.environ["DATABRICKS_WAREHOUSE_ID"].strip()

HTTP_PATH = f"/sql/1.0/warehouses/{DATABRICKS_WAREHOUSE_ID}"
REST_BASE_URL = cfg.host.rstrip("/")


def _auth_headers() -> dict:
    token = request_context.user_token.get()
    if token:
        return {"Authorization": f"Bearer {token}"}
    return cfg.authenticate()


def _sp_credentials_provider():
    return cfg.authenticate


# Opening a SQL Warehouse connection (auth + session setup) is far slower
# than running a query on it, so connections are pooled and reused instead
# of opened per call. A single page load fires several panel requests at
# once, and build_full_tree()/fetch_grants() each fan out to several more
# threads on top of that - if each of those got its own connection, a
# single page load could try to open a dozen-plus brand new sessions in the
# same instant, which the warehouse throttles/rejects (surfaces as a
# RequestError from open_session), even though each individual connection
# is fine once established. Bounding the pool to a small fixed size means
# at most POOL_SIZE sessions ever get opened for the app's whole lifetime.
#
# This pool only ever holds service-principal/local-dev connections now -
# see run_query's viewer-token branch below for why viewer-scoped queries
# don't use this connector at all.
POOL_SIZE = 4

_sp_pool: queue.Queue = queue.Queue(maxsize=POOL_SIZE)
for _ in range(POOL_SIZE):
    _sp_pool.put(None)  # None = "slot reserved, connection not opened yet"


def _open_connection():
    return sql.connect(
        server_hostname=cfg.host,
        http_path=HTTP_PATH,
        credentials_provider=_sp_credentials_provider,
    )


# ---------------------------------------------------------------------------
# Viewer-scoped queries: SQL Statement Execution REST API
# ---------------------------------------------------------------------------
# Databricks Apps' on-behalf-of-user token is a scoped OAuth token meant for
# REST API calls (matching the api_scopes declared in app.yaml - sql:execute
# for this one) - it is NOT usable to open a Thrift-protocol SQL Warehouse
# session, which is what databricks-sql-connector's sql.connect() does below
# for the service-principal path. Concretely: opening a session that way
# with a viewer token fails with databricks.sql.exc.RequestError at
# open_session, even when the viewer has "Can Use" on the warehouse and
# every relevant Unity Catalog grant.
#
# sql:execute is the scope for the SQL Statement Execution REST API
# (/api/2.0/sql/statements) instead - a plain bearer-token HTTP call, same
# shape as rest_get/rest_post below - so viewer-scoped queries go through
# that API rather than the connector.
STATEMENT_API_PATH = "/api/2.0/sql/statements"
STATEMENT_POLL_SECONDS = 1.5
STATEMENT_MAX_WAIT_SECONDS = 120


def _statement_parameters(params: dict | tuple) -> list[dict]:
    if not params:
        return []
    if not isinstance(params, dict):
        raise TypeError("Viewer-scoped queries only support named (dict) parameters, e.g. run_query(sql, {'x': 1})")
    return [{"name": name, "value": None if value is None else str(value), "type": "STRING"} for name, value in params.items()]


def _statement_chunk_rows(statement_id: str, columns: list[str], result: dict, headers: dict) -> list[dict]:
    rows: list[list] = list(result.get("data_array") or [])
    next_index = result.get("next_chunk_index")
    while next_index is not None:
        resp = requests.get(
            f"{REST_BASE_URL}{STATEMENT_API_PATH}/{statement_id}/result/chunks/{next_index}",
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        chunk = resp.json()
        rows.extend(chunk.get("data_array") or [])
        next_index = chunk.get("next_chunk_index")
    return [dict(zip(columns, row)) for row in rows]


def _run_query_as_viewer(query: str, params: dict | tuple, token: str) -> list[dict]:
    headers = {"Authorization": f"Bearer {token}"}
    body = {
        "warehouse_id": DATABRICKS_WAREHOUSE_ID,
        "statement": query,
        "parameters": _statement_parameters(params),
        "wait_timeout": "10s",
        "format": "JSON_ARRAY",
        "disposition": "INLINE",
    }
    resp = requests.post(f"{REST_BASE_URL}{STATEMENT_API_PATH}", headers=headers, json=body, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    statement_id = payload["statement_id"]

    deadline = time.monotonic() + STATEMENT_MAX_WAIT_SECONDS
    while payload["status"]["state"] in ("PENDING", "RUNNING"):
        if time.monotonic() > deadline:
            raise TimeoutError(f"Statement {statement_id} did not complete within {STATEMENT_MAX_WAIT_SECONDS}s")
        time.sleep(STATEMENT_POLL_SECONDS)
        resp = requests.get(f"{REST_BASE_URL}{STATEMENT_API_PATH}/{statement_id}", headers=headers, timeout=30)
        resp.raise_for_status()
        payload = resp.json()

    state = payload["status"]["state"]
    if state != "SUCCEEDED":
        error = payload["status"].get("error") or {}
        raise RuntimeError(f"Statement {statement_id} ended in state {state}: {error.get('message', error)}")

    columns = [c["name"] for c in (payload.get("manifest") or {}).get("schema", {}).get("columns", [])]
    return _statement_chunk_rows(statement_id, columns, payload.get("result") or {}, headers)


def run_query(query: str, params: dict | tuple = ()) -> list[dict]:
    token = request_context.user_token.get()
    if token:
        return _run_query_as_viewer(query, params, token)

    conn = _sp_pool.get()
    try:
        if conn is None:
            conn = _open_connection()
        for attempt in (1, 2):
            try:
                with conn.cursor() as cursor:
                    cursor.execute(query, params)
                    columns = [c[0] for c in cursor.description]
                    return [dict(zip(columns, row)) for row in cursor.fetchall()]
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
                if attempt == 2:
                    conn = None
                    raise
                time.sleep(1)
                conn = _open_connection()
    finally:
        _sp_pool.put(conn)  # always return a slot, even on failure (as None, so it reopens lazily next time)


def rest_get(path: str, params: dict | None = None, timeout: int = 30) -> dict:
    """GET a single-object Databricks REST API endpoint and return the parsed body."""
    resp = requests.get(
        f"{REST_BASE_URL}{path}",
        headers=_auth_headers(),
        params=params,
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()


def rest_post(path: str, json_body: dict, timeout: int = 30) -> dict:
    """POST to a Databricks REST API endpoint (e.g. a model serving endpoint) and return the parsed body."""
    resp = requests.post(
        f"{REST_BASE_URL}{path}",
        headers=_auth_headers(),
        json=json_body,
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()


def rest_get_all(path: str, items_key: str, params: dict | None = None, max_pages: int = 10) -> list[dict]:
    """GET a paginated Databricks REST API list endpoint and return all items."""
    items: list[dict] = []
    query = dict(params or {})
    for _ in range(max_pages):
        resp = requests.get(
            f"{REST_BASE_URL}{path}",
            headers=_auth_headers(),
            params=query,
            timeout=30,
        )
        resp.raise_for_status()
        body = resp.json()
        items.extend(body.get(items_key, []))
        token = body.get("next_page_token")
        if not token:
            break
        query["page_token"] = token
    return items


def scim_get_all(path: str, page_size: int = 100) -> list[dict]:
    """GET a paginated Databricks SCIM 2.0 endpoint (Users/Groups) and return all Resources."""
    resources: list[dict] = []
    start_index = 1
    while True:
        resp = requests.get(
            f"{REST_BASE_URL}{path}",
            headers=_auth_headers(),
            params={"startIndex": start_index, "count": page_size},
            timeout=30,
        )
        resp.raise_for_status()
        body = resp.json()
        batch = body.get("Resources", [])
        resources.extend(batch)
        total = body.get("totalResults", len(resources))
        start_index += page_size
        if not batch or start_index > total:
            break
    return resources
