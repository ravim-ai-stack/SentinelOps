"""
Thin wrapper around databricks-sql-connector for querying Unity Catalog
metadata (system.information_schema) through a SQL Warehouse.

Auth is resolved by databricks-sdk's Config(), which picks whatever
credentials are available without any code branching:
  - local dev: DATABRICKS_HOST / DATABRICKS_TOKEN from backend/.env
  - deployed as a Databricks App: the app's service-principal OAuth
    credentials, injected automatically by the platform (no .env, no
    secret ever committed). Browser-level login to the app itself is
    also handled by the platform (workspace SSO) - not this file.
"""

import os
import threading
import time

import requests
from databricks import sql
from databricks.sdk.core import Config
from dotenv import load_dotenv

load_dotenv()

cfg = Config()

DATABRICKS_WAREHOUSE_ID = os.environ["DATABRICKS_WAREHOUSE_ID"].strip()

HTTP_PATH = f"/sql/1.0/warehouses/{DATABRICKS_WAREHOUSE_ID}"
REST_BASE_URL = cfg.host.rstrip("/")


def _credentials_provider():
    return cfg.authenticate


# Opening a SQL Warehouse connection (auth + session setup) is far slower
# than running a query on it, so each request thread keeps its own
# connection open and reuses it across queries instead of reconnecting
# every time - reconnecting only if that connection has gone bad.
_local = threading.local()

# A single page load fires several panel requests at once, and
# build_full_tree() alone fans out to 10 more threads on top of that -
# each of those, on first use, tries to open its own brand new SQL
# Warehouse session. Opening many sessions in the same instant gets
# throttled/rejected by the warehouse (surfaces as a RequestError from
# open_session), even though each individual connection is fine once
# established. This caps how many session-opens can be in flight at
# once; it only gates the (slow) connect step, not query execution on
# already-open connections.
_connect_gate = threading.Semaphore(4)


def _get_thread_connection():
    conn = getattr(_local, "conn", None)
    if conn is None:
        with _connect_gate:
            conn = sql.connect(
                server_hostname=cfg.host,
                http_path=HTTP_PATH,
                credentials_provider=_credentials_provider,
            )
        _local.conn = conn
    return conn


def _drop_thread_connection():
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        _local.conn = None


def run_query(query: str, params: dict | tuple = ()) -> list[dict]:
    for attempt in (1, 2):
        conn = _get_thread_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(query, params)
                columns = [c[0] for c in cursor.description]
                return [dict(zip(columns, row)) for row in cursor.fetchall()]
        except Exception:
            _drop_thread_connection()
            if attempt == 2:
                raise
            time.sleep(1)


def rest_get(path: str, params: dict | None = None, timeout: int = 30) -> dict:
    """GET a single-object Databricks REST API endpoint and return the parsed body."""
    resp = requests.get(
        f"{REST_BASE_URL}{path}",
        headers=cfg.authenticate(),
        params=params,
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()


def rest_post(path: str, json_body: dict, timeout: int = 30) -> dict:
    """POST to a Databricks REST API endpoint (e.g. a model serving endpoint) and return the parsed body."""
    resp = requests.post(
        f"{REST_BASE_URL}{path}",
        headers=cfg.authenticate(),
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
            headers=cfg.authenticate(),
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
            headers=cfg.authenticate(),
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
