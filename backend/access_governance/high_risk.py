"""Access governance page — 'High risk users' panel."""

from fastapi import APIRouter

from access_service import compute_user_access
from grants_service import build_user_groups, fetch_grants, fetch_scim_groups, fetch_scim_users

router = APIRouter()


@router.get("/api/access/high-risk")
def high_risk():
    users = fetch_scim_users()
    groups = fetch_scim_groups()
    grants = fetch_grants()
    user_groups = build_user_groups(users, groups)

    user_access = compute_user_access(users, user_groups, grants)
    risky = sorted(
        (u for u in user_access if u["risk_level"]),
        key=lambda u: (u["risk_level"] != "high", -u["high_risk_objects"]),
    )
    return {"high_risk": risky}
