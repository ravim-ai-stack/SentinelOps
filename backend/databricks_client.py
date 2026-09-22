# backend/databricks_client.py
import os
import logging
import time
import requests
import databricks.sql as sql

logger = logging.getLogger("sentinelops")

# ---------------------------------------------------------------------------
# Connection helpers (kept for backward compat; catalog_service no longer uses)
# ---------------------------------------------------------------------------

def _open_connection(token: str):
    """Open a SQL connector session using env vars injected by the Apps
    platform when a ``sql_warehouse`` resource is declared in app.yaml.

    Reads ``DATABRICKS_HTTP_PATH`` directly; if not present, constructs it
    from ``DATABRICKS_SQL_WAREHOUSE_ID`` so nothing is hardcoded.
    """
    host = os.environ["DATABRICKS_HOST"].replace("https://", "").rstrip("/")
    http_path = os.environ.get("DATABRICKS_HTTP_PATH")
    if not http_path:
        warehouse_id = os.environ["DATABRICKS_SQL_WAREHOUSE_ID"]
        http_path = f"/sql/1.0/warehouses/{warehouse_id}"
    return sql.connect(
        server_hostname=host,
        http_path=http_path,
        access_token=token,
    )

def run_query(query: str, token: str | None = None):
    """Execute a SQL statement via the SQL connector. Kept for backward
    compatibility — catalog_service.py now uses rest_get_all instead."""
    if token is None:
        token = os.environ.get("DATABRICKS_TOKEN", "")
    conn = _open_connection(token)
    try:
        cursor = conn.cursor()
        cursor.execute(query)
        columns = [d[0] for d in cursor.description] if cursor.description else []
        rows = cursor.fetchall()
        return [dict(zip(columns, r)) for r in rows]
    finally:
        conn.close()

# ---------------------------------------------------------------------------
# REST API helpers (used by both jobs module and catalog service)
# ---------------------------------------------------------------------------

def _rest_host():
    host = os.environ.get("DATABRICKS_HOST", "")
    if not host.startswith("http"):
        host = "https://" + host
    return host.rstrip("/")

def _rest_token():
    return os.environ.get("DATABRICKS_TOKEN", "")

def rest_get(endpoint: str, params: dict | None = None) -> dict:
    """Single REST GET call. Returns the parsed JSON body."""
    url = _rest_host() + endpoint
    headers = {"Authorization": f"Bearer {_rest_token()}"}
    resp = requests.get(url, headers=headers, params=params or {}, timeout=30)
    resp.raise_for_status()
    return resp.json()

def rest_get_all(
    endpoint: str,
    result_key: str,
    base_params: dict | None = None,
    max_pages: int = 50,
) -> list[dict]:
    """Paginated REST GET. Handles two pagination styles:

    1. Jobs API: uses ``page_token`` query param + ``next_page_token`` field + ``has_more``.
    2. Unity Catalog API: uses ``page_token`` query param + ``next_page_token`` field.

    Both styles are supported transparently.
    """
    base_params = dict(base_params or {})
    all_items: list[dict] = []
    page_token = None

    for _ in range(max_pages):
        params = dict(base_params)
        if page_token:
            params["page_token"] = page_token

        body = rest_get(endpoint, params)
        items = body.get(result_key, [])
        all_items.extend(items)

        page_token = body.get("next_page_token")
        if not page_token:
            # Fall back to has_more check (Jobs API)
            if not body.get("has_more", False):
                break
            # If has_more but no next_page_token, try next_page_token alias
            page_token = body.get("next_page_token")
            if not page_token:
                break

    return all_items