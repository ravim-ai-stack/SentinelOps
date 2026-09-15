"""Dashboard page — 'Most Frequently Used Catalog' bars.

Live — Unity Catalog access lineage (system.access.table_lineage) for
the last 30 days (falls back to an empty list if that system table
isn't enabled on this workspace's metastore).
"""

from fastapi import APIRouter

from dashboard_service import fetch_top_catalogs_by_usage

router = APIRouter()


@router.get("/api/dashboard/top-catalogs")
def top_catalogs():
    return {"catalogs": fetch_top_catalogs_by_usage(days=30, limit=5)}
