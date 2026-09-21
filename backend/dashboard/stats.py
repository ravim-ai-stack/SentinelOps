"""Dashboard page — the 5 stat cards (catalogs/schemas/tables/users/groups)."""

from fastapi import APIRouter

from catalog_service import build_full_tree, summarize
from dashboard_service import fetch_current_user
from grants_service import fetch_scim_groups, fetch_scim_users

router = APIRouter()


@router.get("/api/dashboard/stats")
def dashboard_stats():
    catalog_summary = summarize(build_full_tree())
    return {
        "catalogs": catalog_summary["catalogs"],
        "schemas": catalog_summary["schemas"],
        "tables": catalog_summary["tables"],
        "users": len(fetch_scim_users()),
        "groups": len(fetch_scim_groups()),
        "current_user": fetch_current_user(),
    }
