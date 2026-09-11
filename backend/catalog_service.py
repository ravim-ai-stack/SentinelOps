"""
Catalog Explorer service — builds the catalog -> schema -> object tree from
Unity Catalog metadata (system.information_schema) plus the Unity Catalog
REST API (for registered models).
"""

import logging
from typing import Optional

from cache import ttl_cache
from databricks_client import rest_get_all, run_query

logger = logging.getLogger("sentinelops.catalog")

# Catalog explorer's stats/tree panels, and Data security's sensitive-tables/
# recent-classifications panels, each independently need the full table
# list - cache briefly so one page load makes one Databricks round-trip.
CACHE_SECONDS = 8


def fetch_catalogs() -> dict[str, dict]:
    rows = run_query(
        """
        SELECT catalog_name, catalog_owner, comment, last_altered
        FROM system.information_schema.catalogs
        """
    )
    return {r["catalog_name"]: r for r in rows}


def fetch_schemata() -> dict[str, dict]:
    rows = run_query(
        """
        SELECT catalog_name, schema_name, schema_owner, comment, last_altered
        FROM system.information_schema.schemata
        WHERE schema_name != 'information_schema'
        """
    )
    return {f"{r['catalog_name']}.{r['schema_name']}": r for r in rows}


@ttl_cache(CACHE_SECONDS)
def fetch_tables_and_views() -> list[dict]:
    rows = run_query(
        """
        SELECT table_catalog AS catalog, table_schema AS schema, table_name AS name,
               table_type, table_owner AS owner, last_altered
        FROM system.information_schema.tables
        WHERE table_schema != 'information_schema'
        """
    )
    out = []
    for r in rows:
        out.append({
            "catalog": r["catalog"],
            "schema": r["schema"],
            "name": r["name"],
            "kind": "VIEW" if r["table_type"] == "VIEW" else "TABLE",
            "type": r["table_type"],
            "owner": r["owner"],
            "last_altered": r["last_altered"],
        })
    return out


def fetch_functions() -> list[dict]:
    rows = run_query(
        """
        SELECT routine_catalog AS catalog, routine_schema AS schema, routine_name AS name,
               routine_owner AS owner, routine_type, last_altered
        FROM system.information_schema.routines
        WHERE routine_schema != 'information_schema'
        """
    )
    return [
        {
            "catalog": r["catalog"],
            "schema": r["schema"],
            "name": r["name"],
            "kind": "FUNCTION",
            "type": r["routine_type"] or "FUNCTION",
            "owner": r["owner"],
            "last_altered": r["last_altered"],
        }
        for r in rows
    ]


def fetch_volumes() -> list[dict]:
    try:
        rows = run_query(
            """
            SELECT volume_catalog AS catalog, volume_schema AS schema, volume_name AS name,
                   volume_type, volume_owner AS owner, last_altered
            FROM system.information_schema.volumes
            WHERE volume_schema != 'information_schema'
            """
        )
    except Exception:
        logger.warning("Volumes not available on this workspace", exc_info=True)
        return []
    return [
        {
            "catalog": r["catalog"],
            "schema": r["schema"],
            "name": r["name"],
            "kind": "VOLUME",
            "type": r["volume_type"],
            "owner": r["owner"],
            "last_altered": r["last_altered"],
        }
        for r in rows
    ]


def fetch_models() -> list[dict]:
    """Unity Catalog registered models — via REST since information_schema
    has no models table. Skipped gracefully if UC model registry isn't
    enabled/accessible for this token."""
    try:
        models = rest_get_all("/api/2.1/unity-catalog/models", "registered_models")
    except Exception:
        logger.warning("Registered models not available", exc_info=True)
        return []
    return [
        {
            "catalog": m["catalog_name"],
            "schema": m["schema_name"],
            "name": m["name"],
            "kind": "MODEL",
            "type": m.get("securable_kind", "MODEL"),
            "owner": m.get("owner"),
            "last_altered": m.get("updated_at"),  # epoch millis
        }
        for m in models
    ]


# ---------------------------------------------------------------------------
# Tree assembly + search
# ---------------------------------------------------------------------------

@ttl_cache(CACHE_SECONDS)
def build_full_tree() -> list[dict]:
    catalogs = fetch_catalogs()
    schemata = fetch_schemata()
    objects = fetch_tables_and_views() + fetch_functions() + fetch_volumes() + fetch_models()

    schema_buckets: dict[str, dict] = {}
    for key, s in schemata.items():
        schema_buckets[key] = {
            "schema": s["schema_name"],
            "owner": s["schema_owner"],
            "last_altered": s["last_altered"],
            "objects": [],
        }

    for obj in objects:
        key = f"{obj['catalog']}.{obj['schema']}"
        bucket = schema_buckets.setdefault(key, {
            "schema": obj["schema"], "owner": None, "last_altered": None, "objects": [],
        })
        bucket["objects"].append({
            "name": obj["name"],
            "kind": obj["kind"],
            "type": obj["type"],
            "owner": obj["owner"],
            "last_altered": obj["last_altered"],
        })

    catalog_buckets: dict[str, dict] = {}
    for key, bucket in schema_buckets.items():
        catalog_name = key.split(".", 1)[0]
        cat = catalog_buckets.setdefault(catalog_name, {
            "catalog": catalog_name,
            "owner": catalogs.get(catalog_name, {}).get("catalog_owner"),
            "schemas": [],
        })
        cat["schemas"].append(bucket)

    for catalog_name, cat_info in catalogs.items():
        catalog_buckets.setdefault(catalog_name, {
            "catalog": catalog_name,
            "owner": cat_info.get("catalog_owner"),
            "schemas": [],
        })

    tree = list(catalog_buckets.values())
    for cat in tree:
        cat["schemas"].sort(key=lambda s: s["schema"])
        for s in cat["schemas"]:
            s["objects"].sort(key=lambda o: (o["kind"], o["name"]))
    tree.sort(key=lambda c: c["catalog"])
    return tree


def _match(name: Optional[str], needle: str) -> bool:
    return bool(name) and needle in name.lower()


def filter_tree(tree: list[dict], search: Optional[str]) -> list[dict]:
    if not search:
        return tree
    needle = search.strip().lower()
    if not needle:
        return tree

    filtered = []
    for cat in tree:
        catalog_match = _match(cat["catalog"], needle)
        kept_schemas = []
        for schema in cat["schemas"]:
            schema_match = _match(schema["schema"], needle)
            if catalog_match or schema_match:
                kept_objects = schema["objects"]
            else:
                kept_objects = [o for o in schema["objects"] if _match(o["name"], needle)]
            if kept_objects or catalog_match or schema_match:
                kept_schemas.append({**schema, "objects": kept_objects})
        if kept_schemas or catalog_match:
            filtered.append({**cat, "schemas": kept_schemas})
    return filtered


def summarize(tree: list[dict]) -> dict:
    schema_count = sum(len(c["schemas"]) for c in tree)
    objects = [o for c in tree for s in c["schemas"] for o in s["objects"]]
    return {
        "catalogs": len(tree),
        "schemas": schema_count,
        "tables": sum(1 for o in objects if o["kind"] in ("TABLE", "VIEW")),
        "functions": sum(1 for o in objects if o["kind"] == "FUNCTION"),
        "volumes": sum(1 for o in objects if o["kind"] == "VOLUME"),
        "models": sum(1 for o in objects if o["kind"] == "MODEL"),
        "objects": len(objects),
    }
