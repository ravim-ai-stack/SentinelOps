"""Catalog explorer page — per-catalog 'View access' drill-down panel."""

from fastapi import APIRouter

from grants_service import (
    build_group_members,
    fetch_grants,
    fetch_scim_groups,
    fetch_scim_users,
    resolve_table_access,
    resolve_table_access_detail,
)

router = APIRouter()


@router.get("/api/catalog/{catalog}/access")
def catalog_access(catalog: str):
    users = fetch_scim_users()
    groups = fetch_scim_groups()
    grants = fetch_grants()
    group_member_count = {g.get("displayName"): len(g.get("members", [])) for g in groups}
    group_members = build_group_members(users, groups)

    grantees = {g["grantee"] for g in grants if g["level"] == "CATALOG" and g["object"] == catalog}
    detail = resolve_table_access_detail(grantees, group_member_count, group_members)

    return {
        "catalog": catalog,
        "access": resolve_table_access(grantees, group_member_count),
        "groups": detail["groups"],
        "users": detail["users"],
    }
