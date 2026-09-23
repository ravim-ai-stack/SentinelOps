"""Dashboard page — 'Most Frequently Used Catalog' bars.

Live — Unity Catalog access lineage (system.access.table_lineage) for
the last 30 days (falls back to an empty list if that system table
isn't enabled on this workspace's metastore).
"""

from fastapi import APIRouter, Request

from dashboard_service import fetch_top_catalogs_by_usage

router = APIRouter()


@router.get("/api/dashboard/top-catalogs")
def top_catalogs(request: Request):
    # Pass the request so the lineage query runs with the viewer's
    # forwarded token - the app's service principal can't read system.access.
    return {"catalogs": fetch_top_catalogs_by_usage(request, days=30, limit=5)}
