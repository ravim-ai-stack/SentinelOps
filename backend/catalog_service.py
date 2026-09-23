"""
Catalog Explorer service — builds the catalog -> schema -> object tree from
Unity Catalog metadata (system.information_schema) plus the Unity Catalog
REST API (for registered models).
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from cache import ttl_cache
from databricks_client import rest_get_all, run_query, preserve_context

logger = logging.getLogger("sentinelops.catalog")

# Catalog explorer's stats/tree panels, and Data security's sensitive-tables/
# recent-classifications panels, each independently need the full table
# list - cache so repeated panel loads and filter/tag changes within a
# live-refresh cycle (30s on the frontend) reuse one Databricks round-trip
# instead of re-querying the warehouse on every interaction.
CACHE_SECONDS = 30

# Long-lived (not created/torn down per call) so its worker threads keep
# their warm databricks_client connections across repeated calls to
# build_full_tree() - see the reasoning on _tree_pool below. Kept small
# (rather than one thread per fetch_* call) since each new thread opens
# its own SQL Warehouse session on first use, and opening many sessions
# in the same instant gets throttled by the warehouse - see
# databricks_client._connect_gate, which this pool's size works with.
_tree_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="catalog-tree")


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


def _label(tag_name: str, tag_value: Optional[str]) -> str:
    return f"{tag_name}: {tag_value}" if tag_value else tag_name


@ttl_cache(CACHE_SECONDS)
def fetch_catalog_tags() -> dict[str, list[str]]:
    """Unity Catalog governed tags set directly on catalogs, keyed by catalog name."""
    try:
        rows = run_query(
            "SELECT catalog_name, tag_name, tag_value FROM system.information_schema.catalog_tags"
        )
    except Exception:
        logger.warning("Catalog tags not available on this workspace", exc_info=True)
        return {}
    by_catalog: dict[str, list[str]] = {}
    for r in rows:
        by_catalog.setdefault(r["catalog_name"], []).append(_label(r["tag_name"], r.get("tag_value")))
    for tags in by_catalog.values():
        tags.sort()
    return by_catalog


@ttl_cache(CACHE_SECONDS)
def fetch_schema_tags() -> dict[str, list[str]]:
    """Unity Catalog governed tags set directly on schemas, keyed by 'catalog.schema'."""
    try:
        rows = run_query(
            "SELECT catalog_name, schema_name, tag_name, tag_value FROM system.information_schema.schema_tags"
        )
    except Exception:
        logger.warning("Schema tags not available on this workspace", exc_info=True)
        return {}
    by_schema: dict[str, list[str]] = {}
    for r in rows:
        key = f"{r['catalog_name']}.{r['schema_name']}"
        by_schema.setdefault(key, []).append(_label(r["tag_name"], r.get("tag_value")))
    for tags in by_schema.values():
        tags.sort()
    return by_schema


@ttl_cache(CACHE_SECONDS)
def fetch_table_tags() -> dict[str, list[str]]:
    """Unity Catalog governed tags on tables, keyed by 'catalog.schema.table'.
    Not every workspace has tagging enabled, so this degrades to no tags
    rather than failing the whole tree/PII scan."""
    try:
        rows = run_query(
            """
            SELECT catalog_name, schema_name, table_name, tag_name, tag_value
            FROM system.information_schema.table_tags
            """
        )
    except Exception:
        logger.warning("Table tags not available on this workspace", exc_info=True)
        return {}
    by_table: dict[str, list[str]] = {}
    for r in rows:
        key = f"{r['catalog_name']}.{r['schema_name']}.{r['table_name']}"
        by_table.setdefault(key, []).append(_label(r["tag_name"], r.get("tag_value")))
    for tags in by_table.values():
        tags.sort()
    return by_table


@ttl_cache(CACHE_SECONDS)
def _fetch_column_tag_rows() -> list[dict]:
    try:
        return run_query(
            """
            SELECT catalog_name, schema_name, table_name, column_name, tag_name, tag_value
            FROM system.information_schema.column_tags
            """
        )
    except Exception:
        logger.warning("Column tags not available on this workspace", exc_info=True)
        return []


@ttl_cache(CACHE_SECONDS)
def fetch_column_tags() -> dict[str, list[str]]:
    """Unity Catalog governed tags on columns (e.g. auto-applied PII
    classifications like class.email_address), rolled up to their owning
    table and keyed by 'catalog.schema.table'. Tables are frequently tagged
    at the column level rather than the table level, so these are merged
    into an object's tags alongside fetch_table_tags()."""
    by_table: dict[str, set[str]] = {}
    for r in _fetch_column_tag_rows():
        key = f"{r['catalog_name']}.{r['schema_name']}.{r['table_name']}"
        by_table.setdefault(key, set()).add(_label(r["tag_name"], r.get("tag_value")))
    return {key: sorted(tags) for key, tags in by_table.items()}


@ttl_cache(CACHE_SECONDS)
def fetch_column_tag_details() -> dict[str, list[dict]]:
    """Same source as fetch_column_tags(), but keeping each column's name
    alongside its tag instead of collapsing to just the tag labels - for
    views (e.g. per-user access) that need to show which column a tag
    applies to, not just that the table has one."""
    by_table: dict[str, list[dict]] = {}
    for r in _fetch_column_tag_rows():
        key = f"{r['catalog_name']}.{r['schema_name']}.{r['table_name']}"
        by_table.setdefault(key, []).append({
            "column": r["column_name"],
            "tag": _label(r["tag_name"], r.get("tag_value")),
        })
    for rows in by_table.values():
        rows.sort(key=lambda x: (x["column"], x["tag"]))
    return by_table


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
    # Each fetch_* below is an independent Databricks round-trip (its own SQL
    # connection, or a REST call for models) that only reads data - run them
    # concurrently, on the shared _tree_pool, so the tree's wall-clock cost is
    # the slowest single call rather than the sum of all of them.
    pool = _tree_pool
    # CRITICAL: Wrap all fetch functions with preserve_context() so the user token
    # ContextVar is propagated to worker threads. Without this, worker threads see
    # get_user_token() = None and fall back to the service principal.
    catalogs_f = pool.submit(preserve_context(fetch_catalogs))
    schemata_f = pool.submit(preserve_context(fetch_schemata))
    tables_f = pool.submit(preserve_context(fetch_tables_and_views))
    functions_f = pool.submit(preserve_context(fetch_functions))
    volumes_f = pool.submit(preserve_context(fetch_volumes))
    models_f = pool.submit(preserve_context(fetch_models))
    catalog_tags_f = pool.submit(preserve_context(fetch_catalog_tags))
    schema_tags_f = pool.submit(preserve_context(fetch_schema_tags))
    table_tags_f = pool.submit(preserve_context(fetch_table_tags))
    column_tags_f = pool.submit(preserve_context(fetch_column_tags))

    catalogs = catalogs_f.result()
    schemata = schemata_f.result()
    objects = tables_f.result() + functions_f.result() + volumes_f.result() + models_f.result()
    catalog_tags = catalog_tags_f.result()
    schema_tags = schema_tags_f.result()
    table_tags = table_tags_f.result()
    column_tags = column_tags_f.result()

    schema_buckets: dict[str, dict] = {}
    for key, s in schemata.items():
        schema_buckets[key] = {
            "schema": s["schema_name"],
            "owner": s["schema_owner"],
            "last_altered": s["last_altered"],
            "objects": [],
            "tags": schema_tags.get(key, []),
        }

    for obj in objects:
        key = f"{obj['catalog']}.{obj['schema']}"
        bucket = schema_buckets.setdefault(key, {
            "schema": obj["schema"], "owner": None, "last_altered": None, "objects": [],
            "tags": schema_tags.get(key, []),
        })
        table_key = f"{obj['catalog']}.{obj['schema']}.{obj['name']}"
        bucket["objects"].append({
            "name": obj["name"],
            "kind": obj["kind"],
            "type": obj["type"],
            "owner": obj["owner"],
            "last_altered": obj["last_altered"],
            "tags": sorted(set(table_tags.get(table_key, [])) | set(column_tags.get(table_key, []))),
        })

    catalog_buckets: dict[str, dict] = {}
    for key, bucket in schema_buckets.items():
        catalog_name = key.split(".", 1)[0]
        cat = catalog_buckets.setdefault(catalog_name, {
            "catalog": catalog_name,
            "owner": catalogs.get(catalog_name, {}).get("catalog_owner"),
            "schemas": [],
            "tags": catalog_tags.get(catalog_name, []),
        })
        cat["schemas"].append(bucket)

    for catalog_name, cat_info in catalogs.items():
        catalog_buckets.setdefault(catalog_name, {
            "catalog": catalog_name,
            "owner": cat_info.get("catalog_owner"),
            "schemas": [],
            "tags": catalog_tags.get(catalog_name, []),
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


def filter_tree(tree: list[dict], search: Optional[str], tag: Optional[str] = None) -> list[dict]:
    needle = (search or "").strip().lower()
    if not needle and not tag:
        return tree

    def tag_ok(obj: dict) -> bool:
        return not tag or tag in (obj.get("tags") or [])

    filtered = []
    for cat in tree:
        catalog_match = bool(needle and _match(cat["catalog"], needle))
        catalog_tag_match = bool(tag and tag in (cat.get("tags") or []))
        kept_schemas = []
        for schema in cat["schemas"]:
            schema_match = bool(needle and _match(schema["schema"], needle))
            # A tag set directly on the catalog or the schema covers every
            # object underneath it, even if no individual object carries it.
            schema_tag_match = catalog_tag_match or bool(tag and tag in (schema.get("tags") or []))
            if needle and (catalog_match or schema_match):
                candidate_objects = schema["objects"]
            elif needle:
                candidate_objects = [o for o in schema["objects"] if _match(o["name"], needle)]
            else:
                candidate_objects = schema["objects"]
            kept_objects = candidate_objects if schema_tag_match else [o for o in candidate_objects if tag_ok(o)]
            # A bare name match with no tag filter still surfaces the (possibly
            # empty) catalog/schema; a tag filter surfaces it when the
            # catalog/schema itself carries the tag, even with no objects.
            keep_empty_schema = schema_tag_match or ((catalog_match or schema_match) and not tag)
            if kept_objects or keep_empty_schema:
                kept_schemas.append({**schema, "objects": kept_objects})
        keep_empty_catalog = catalog_tag_match or (catalog_match and not tag)
        if kept_schemas or keep_empty_catalog:
            filtered.append({**cat, "schemas": kept_schemas})
    return filtered


def list_distinct_tags(tree: list[dict]) -> list[str]:
    tags: set[str] = set()
    for cat in tree:
        tags.update(cat.get("tags") or [])
        for s in cat["schemas"]:
            tags.update(s.get("tags") or [])
            for o in s["objects"]:
                tags.update(o.get("tags") or [])
    return sorted(tags)


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
