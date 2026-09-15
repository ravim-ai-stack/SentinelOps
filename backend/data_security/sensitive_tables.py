"""Data security page — 'Sensitive tables' panel (table list + per-table
access drill-down). The only Data security panel that needs grants/SCIM,
since it shows who has access to each sensitive table."""

from fastapi import APIRouter

from catalog_service import fetch_tables_and_views
from grants_service import build_group_members, fetch_grants, fetch_scim_groups, fetch_scim_users
from security_service import build_pii_columns, build_sensitive_tables

router = APIRouter()


@router.get("/api/security/sensitive-tables")
def sensitive_tables():
    pii_columns = build_pii_columns()
    table_meta = {
        f"{t['catalog']}.{t['schema']}.{t['name']}": t
        for t in fetch_tables_and_views()
    }
    users = fetch_scim_users()
    groups = fetch_scim_groups()
    grants = fetch_grants()
    group_member_count = {g.get("displayName"): len(g.get("members", [])) for g in groups}
    group_members = build_group_members(users, groups)

    tables = build_sensitive_tables(pii_columns, table_meta, grants, group_member_count, group_members)
    return {"sensitive_tables": tables}
