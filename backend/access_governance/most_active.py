"""Access governance page — 'Most active users by access' panel."""

from fastapi import APIRouter

from access_service import compute_user_access
from grants_service import build_user_groups, fetch_grants, fetch_scim_groups, fetch_scim_users

router = APIRouter()


@router.get("/api/access/most-active")
def most_active():
    users = fetch_scim_users()
    groups = fetch_scim_groups()
    grants = fetch_grants()
    user_groups = build_user_groups(users, groups)

    user_access = compute_user_access(users, user_groups, grants)
    user_access.sort(key=lambda u: u["objects_with_access"], reverse=True)
    top = [u for u in user_access if u["objects_with_access"] > 0][:5]
    return {"most_active": top}
