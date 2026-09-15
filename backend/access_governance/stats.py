"""Access governance page — the 4 stat cards."""

from fastapi import APIRouter

from access_service import compute_user_access
from grants_service import build_user_groups, fetch_grants, fetch_scim_groups, fetch_scim_users

router = APIRouter()


@router.get("/api/access/stats")
def access_stats():
    users = fetch_scim_users()
    groups = fetch_scim_groups()
    grants = fetch_grants()
    user_groups = build_user_groups(users, groups)
    user_access = compute_user_access(users, user_groups, grants)

    return {
        "total_users": len(users),
        "total_groups": len(groups),
        "total_grants": len(grants),
        "high_risk_users": sum(1 for u in user_access if u["risk_level"]),
    }
