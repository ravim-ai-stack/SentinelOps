"""Access governance page — Effective access tab (per-user grant resolution)."""

from fastapi import APIRouter

from grants_service import build_user_groups, fetch_grants, fetch_scim_groups, fetch_scim_users

router = APIRouter()


@router.get("/api/access/effective/{user}")
def effective_access(user: str):
    users = fetch_scim_users()
    groups = fetch_scim_groups()
    grants = fetch_grants()
    user_groups = build_user_groups(users, groups).get(user, [])

    rows = []
    for g in grants:
        if g["grantee"] == user:
            rows.append({**g, "via": "Direct"})
        elif g["grantee"] in user_groups:
            rows.append({**g, "via": f"Group: {g['grantee']}"})
    rows.sort(key=lambda g: g["object"])

    return {"user": user, "groups": user_groups, "effective_grants": rows}
