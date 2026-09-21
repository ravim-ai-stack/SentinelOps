"""
Dashboard service — logic unique to the Dashboard page's own panels. Counts
that just reuse another page's data (catalog/schema/table counts, PII
category breakdown, etc.) are computed with that page's own service
functions directly from each panel file, rather than duplicated here.
"""

import logging

from fastapi import Request

from cache import ttl_cache
from databricks_client import run_query
from grants_service import _is_broad, _is_sensitive

logger = logging.getLogger("sentinelops.dashboard")

RISK_LEVELS = ("CATALOG", "SCHEMA", "TABLE")
CACHE_SECONDS = 8


@ttl_cache(CACHE_SECONDS)
def fetch_current_user() -> str:
    """The Databricks identity running these queries (the app's service
    principal once deployed) - used as a local-dev fallback, since running
    outside a Databricks App there's no logged-in viewer to read from
    request headers."""
    rows = run_query("SELECT current_user() AS user")
    return rows[0]["user"]


def resolve_viewer_name(request: Request) -> str:
    """The actual logged-in viewer, for the Dashboard's welcome message.
    Databricks Apps' auth proxy forwards the identity of whoever is
    logged in on every request, regardless of which credentials the
    backend itself uses to query Databricks. Falls back to
    fetch_current_user() locally, where there's no such proxy in front."""
    for header in ("x-forwarded-preferred-username", "x-forwarded-email", "x-forwarded-user"):
        value = request.headers.get(header)
        if value:
            return value
    return fetch_current_user()


def compute_access_risk_by_level(grants: list[dict]) -> dict[str, int]:
    """Count distinct objects with broad or sensitive access, split by
    grant level, for the Dashboard's 'Access Risks' chart."""
    risky_objects: dict[str, set[str]] = {level: set() for level in RISK_LEVELS}
    for g in grants:
        if g["level"] not in risky_objects:
            continue
        if _is_broad(g["level"], g["privilege"]) or _is_sensitive(g["object"]):
            risky_objects[g["level"]].add(g["object"])
    return {level: len(objs) for level, objs in risky_objects.items()}


@ttl_cache(CACHE_SECONDS)
def fetch_top_catalogs_by_usage(days: int = 30, limit: int = 5) -> list[dict]:
    """Catalogs ranked by Unity Catalog access lineage (read + write) in
    the last `days` days, for the 'Most Frequently Used Catalog' chart.
    Excludes Databricks' own 'system' catalog, which otherwise dominates
    every window from internal jobs/monitoring rather than real usage.
    Falls back to an empty list if system.access.table_lineage isn't
    enabled on this workspace's metastore."""
    try:
        rows = run_query(
            f"""
            SELECT cat, sum(n) AS count FROM (
              SELECT source_table_catalog AS cat, count(*) AS n
              FROM system.access.table_lineage
              WHERE source_table_catalog IS NOT NULL
                AND source_table_catalog != 'system'
                AND event_date >= current_date() - INTERVAL {int(days)} DAYS
              GROUP BY source_table_catalog
              UNION ALL
              SELECT target_table_catalog AS cat, count(*) AS n
              FROM system.access.table_lineage
              WHERE target_table_catalog IS NOT NULL
                AND target_table_catalog != 'system'
                AND event_date >= current_date() - INTERVAL {int(days)} DAYS
              GROUP BY target_table_catalog
            )
            GROUP BY cat
            ORDER BY count DESC
            LIMIT {int(limit)}
            """
        )
    except Exception:
        logger.warning("system.access.table_lineage not available on this workspace", exc_info=True)
        return []
    return [{"catalog": r["cat"], "count": r["count"]} for r in rows]
