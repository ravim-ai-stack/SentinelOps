"""
Catalog service - UC metadata fetchers with ThreadPoolExecutor context propagation fix.

Fetches catalogs, schemas, tables, and volumes via UC REST API.
The build_full_tree() function uses ThreadPoolExecutor to parallelize schema/table
fetches. CRITICAL: We use preserve_context() from databricks_client to ensure the
user token ContextVar is propagated to worker threads - otherwise worker threads
fall back to the service principal and can't see user-specific catalogs.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from cache import ttl_cache
from databricks_client import rest_get_all, rest_get, preserve_context

logger = logging.getLogger("sentinelops")

CACHE_SECONDS = 30


@ttl_cache(CACHE_SECONDS)
def fetch_catalogs() -> list[dict]:
    """List all catalogs via UC REST API. Returns [{"name": "...", "comment": "..."}, ...]."""
    try:
        items = rest_get_all("/api/2.1/unity-catalog/catalogs", "catalogs", {})
        return [
            {"name": c.get("name", ""), "comment": c.get("comment", "")}
            for c in items
        ]
    except Exception:
        logger.warning("Could not fetch catalogs via UC REST API", exc_info=True)
        return []


@ttl_cache(CACHE_SECONDS)
def fetch_schemas(catalog: str) -> list[dict]:
    """List schemas in a catalog. Returns [{"name": "...", "catalog": "..."}, ...]."""
    try:
        items = rest_get_all(
            "/api/2.1/unity-catalog/schemas",
            "schemas",
            {"catalog_name": catalog},
        )
        return [
            {"name": s.get("name", ""), "catalog": catalog}
            for s in items
        ]
    except Exception:
        logger.warning(f"Could not fetch schemas for {catalog}", exc_info=True)
        return []


@ttl_cache(CACHE_SECONDS)
def fetch_tables(catalog: str, schema: str) -> list[dict]:
    """List tables in a schema. Returns [{"name": "...", "table_type": "..."}, ...]."""
    try:
        items = rest_get_all(
            "/api/2.1/unity-catalog/tables",
            "tables",
            {"catalog_name": catalog, "schema_name": schema},
        )
        return [
            {
                "name": t.get("name", ""),
                "table_type": t.get("table_type", ""),
                "catalog": catalog,
                "schema": schema,
            }
            for t in items
        ]
    except Exception:
        logger.warning(f"Could not fetch tables for {catalog}.{schema}", exc_info=True)
        return []


@ttl_cache(CACHE_SECONDS)
def fetch_volumes(catalog: str, schema: str) -> list[dict]:
    """List volumes in a schema. Returns [{"name": "...", "volume_type": "..."}, ...]."""
    try:
        items = rest_get_all(
            "/api/2.1/unity-catalog/volumes",
            "volumes",
            {"catalog_name": catalog, "schema_name": schema},
        )
        return [
            {
                "name": v.get("name", ""),
                "volume_type": v.get("volume_type", ""),
                "catalog": catalog,
                "schema": schema,
            }
            for v in items
        ]
    except Exception:
        logger.warning(f"Could not fetch volumes for {catalog}.{schema}", exc_info=True)
        return []


def build_full_tree() -> dict:
    """Build the complete catalog → schema → table/volume tree.
    Uses ThreadPoolExecutor with preserve_context() to ensure the user token
    is propagated to worker threads.
    """
    catalogs = fetch_catalogs()
    if not catalogs:
        return {
            "catalogs": [],
            "totals": {"catalogs": 0, "schemas": 0, "tables": 0, "volumes": 0},
        }

    tree: list[dict] = []
    total_schemas = 0
    total_tables = 0
    total_volumes = 0

    with ThreadPoolExecutor(max_workers=8) as executor:
        # CRITICAL: Wrap fetch_schemas with preserve_context() so worker threads
        # inherit the user token ContextVar. Without this, worker threads see
        # get_user_token() = None and fall back to the service principal.
        schema_futures = {
            executor.submit(preserve_context(fetch_schemas), c["name"]): c
            for c in catalogs
        }

        for sf in as_completed(schema_futures):
            cat = schema_futures[sf]
            schemas = sf.result()
            total_schemas += len(schemas)

            # Submit table + volume fetches for each schema (also wrapped)
            table_futures = {
                executor.submit(
                    preserve_context(fetch_tables), cat["name"], s["name"]
                ): s
                for s in schemas
            }
            vol_futures = {
                executor.submit(
                    preserve_context(fetch_volumes), cat["name"], s["name"]
                ): s
                for s in schemas
            }

            schema_list: list[dict] = []
            for s in schemas:
                schema_list.append({
                    "name": s["name"],
                    "tables": [],
                    "volumes": [],
                })

            # Collect table results
            schema_map = {s["name"]: sl for s, sl in zip(schemas, schema_list)}
            for tf in as_completed(table_futures):
                sch = table_futures[tf]
                tables = tf.result()
                total_tables += len(tables)
                schema_map[sch["name"]]["tables"] = tables

            # Collect volume results
            for vf in as_completed(vol_futures):
                sch = vol_futures[vf]
                volumes = vf.result()
                total_volumes += len(volumes)
                schema_map[sch["name"]]["volumes"] = volumes

            tree.append({
                "name": cat["name"],
                "comment": cat.get("comment", ""),
                "schemas": schema_list,
            })

    return {
        "catalogs": tree,
        "totals": {
            "catalogs": len(catalogs),
            "schemas": total_schemas,
            "tables": total_tables,
            "volumes": total_volumes,
        },
    }


def summarize(tree: dict) -> dict:
    """Flatten the tree into a summary for the dashboard stats card."""
    totals = tree.get("totals", {})
    catalog_names = [c["name"] for c in tree.get("catalogs", [])]
    return {
        "catalog_count": totals.get("catalogs", 0),
        "schema_count": totals.get("schemas", 0),
        "table_count": totals.get("tables", 0),
        "volume_count": totals.get("volumes", 0),
        "catalog_names": catalog_names,
    }
