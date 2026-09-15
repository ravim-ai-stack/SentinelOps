"""Access governance page — Groups tab main table."""

from fastapi import APIRouter

from access_service import compute_group_summaries
from grants_service import fetch_grants, fetch_scim_groups

router = APIRouter()


@router.get("/api/access/groups")
def access_groups():
    groups = fetch_scim_groups()
    grants = fetch_grants()
    return {"groups": compute_group_summaries(groups, grants)}
