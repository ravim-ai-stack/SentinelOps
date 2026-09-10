"""
Thin wrapper around databricks-sql-connector for querying Unity Catalog
metadata (system.information_schema) through a SQL Warehouse.
"""

import os
from contextlib import contextmanager

import requests
from databricks import sql
from dotenv import load_dotenv

load_dotenv()

DATABRICKS_HOST = (
    os.environ["DATABRICKS_HOST"].strip().removeprefix("https://").removeprefix("http://").rstrip("/")
)
DATABRICKS_TOKEN = os.environ["DATABRICKS_TOKEN"].strip()
DATABRICKS_WAREHOUSE_ID = os.environ["DATABRICKS_WAREHOUSE_ID"].strip()

HTTP_PATH = f"/sql/1.0/warehouses/{DATABRICKS_WAREHOUSE_ID}"
REST_BASE_URL = f"https://{DATABRICKS_HOST}"


@contextmanager
def get_connection():
    conn = sql.connect(
        server_hostname=DATABRICKS_HOST,
        http_path=HTTP_PATH,
        access_token=DATABRICKS_TOKEN,
    )
    try:
        yield conn
    finally:
        conn.close()


def run_query(query: str, params: dict | tuple = ()) -> list[dict]:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, params)
            columns = [c[0] for c in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]


def rest_get_all(path: str, items_key: str, params: dict | None = None, max_pages: int = 10) -> list[dict]:
    """GET a paginated Databricks REST API list endpoint and return all items."""
    items: list[dict] = []
    query = dict(params or {})
    for _ in range(max_pages):
        resp = requests.get(
            f"{REST_BASE_URL}{path}",
            headers={"Authorization": f"Bearer {DATABRICKS_TOKEN}"},
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
            headers={"Authorization": f"Bearer {DATABRICKS_TOKEN}"},
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
