"""
Thin wrapper around databricks-sql-connector for querying Unity Catalog
metadata (system.information_schema) through a SQL Warehouse.

Auth picks one of two identities per call, resolved through
request_context (see that module):
  - a forwarded viewer token (request_context.user_token) - present when
    deployed as a Databricks App with User Authorization enabled, forwarded
    by the platform per-request as the logged-in viewer's own OAuth access
    token. Every query then runs, and is scoped by Unity Catalog, as that
    viewer - not the app's own identity - so each teammate only ever sees
    what they've personally been granted.
  - no viewer token (local dev, or User Authorization not enabled): falls
    back to databricks-sdk's Config(), which resolves to DATABRICKS_HOST /
    DATABRICKS_TOKEN from backend/.env locally, or the app's own
    service-principal OAuth credentials when deployed (auto-injected by the
    platform - no .env, no secret ever committed).

Browser-level login to the app itself (workspace SSO) is handled entirely
by the platform - not this file.
"""

import hashlib
import os
import queue
import threading
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


def _viewer_credentials_provider(token: str):
    # Same double-indirection shape databricks-sql-connector expects from
    # credentials_provider (and that cfg.authenticate already has): a
    # zero-arg callable returning another zero-arg callable that returns
    # auth headers. The viewer's forwarded token is fixed for the life of
    # the connection, so the inner callable just closes over it.
    return lambda: (lambda: {"Authorization": f"Bearer {token}"})


# Opening a SQL Warehouse connection (auth + session setup) is far slower
# than running a query on it, so connections are pooled and reused instead
# of opened per call. A single page load fires several panel requests at
# once, and build_full_tree()/fetch_grants() each fan out to several more
# threads on top of that - if each of those got its own connection, a
# single page load could try to open a dozen-plus brand new sessions in the
# same instant, which the warehouse throttles/rejects (surfaces as a
# RequestError from open_session), even though each individual connection
# is fine once established.
#
# A connection is opened under one specific identity (baked into
# credentials_provider at connect time), so it can only be reused for that
# same identity - hence one pool per identity rather than one shared pool:
#   - _sp_pool: the service-principal/local-dev identity (no viewer token).
#   - _viewer_pools: one small pool per distinct forwarded viewer token
#     (keyed by a hash of the token, never the token itself), created
#     lazily on first use and evicted oldest-first once more than
#     MAX_TRACKED_VIEWERS distinct viewers have queried in the app's
#     lifetime - bounding total open sessions across however many teammates
#     have opened the app, the same way POOL_SIZE bounds it for the old
#     single-identity model.
POOL_SIZE = 4
VIEWER_POOL_SIZE = 2
MAX_TRACKED_VIEWERS = 25

_sp_pool: queue.Queue = queue.Queue(maxsize=POOL_SIZE)
for _ in range(POOL_SIZE):
    _sp_pool.put(None)  # None = "slot reserved, connection not opened yet"

_viewer_pools: dict[str, queue.Queue] = {}
_viewer_pool_order: list[str] = []  # oldest-first; re-touched keys move to the end
_viewer_pools_lock = threading.Lock()


def _viewer_key(token: str) -> str:
    # Never keep the raw token as a dict key / in logs - just enough of a
    # hash to tell viewers apart for pooling purposes.
    return hashlib.sha256(token.encode()).hexdigest()[:16]


def _get_pool(token: str | None) -> queue.Queue:
    if not token:
        return _sp_pool

    key = _viewer_key(token)
    with _viewer_pools_lock:
        pool = _viewer_pools.get(key)
        if pool is None:
            pool = queue.Queue(maxsize=VIEWER_POOL_SIZE)
            for _ in range(VIEWER_POOL_SIZE):
                pool.put(None)
            _viewer_pools[key] = pool
            _viewer_pool_order.append(key)
            if len(_viewer_pool_order) > MAX_TRACKED_VIEWERS:
                _evict_oldest_viewer_pool()
        elif key in _viewer_pool_order:
            _viewer_pool_order.remove(key)
            _viewer_pool_order.append(key)
        return pool


def _evict_oldest_viewer_pool() -> None:
    # Caller already holds _viewer_pools_lock. Any connection a live request
    # currently has checked out of the evicted pool is unaffected (it still
    # holds that Queue object directly) - it just won't be reused once
    # returned, since nothing points at this pool going forward.
    oldest_key = _viewer_pool_order.pop(0)
    oldest_pool = _viewer_pools.pop(oldest_key, None)
    if oldest_pool is None:
        return
    while not oldest_pool.empty():
        conn = oldest_pool.get_nowait()
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _open_connection(token: str | None):
    credentials_provider = _viewer_credentials_provider(token) if token else _sp_credentials_provider
    return sql.connect(
        server_hostname=cfg.host,
        http_path=HTTP_PATH,
        credentials_provider=credentials_provider,
    )


def run_query(query: str, params: dict | tuple = ()) -> list[dict]:
    token = request_context.user_token.get()
    pool = _get_pool(token)
    conn = pool.get()
    try:
        if conn is None:
            conn = _open_connection(token)
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
                conn = _open_connection(token)
    finally:
        pool.put(conn)  # always return a slot, even on failure (as None, so it reopens lazily next time)


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
