"""Access governance page — Effective access tab (per-user grant resolution)."""

from fastapi import APIRouter

from catalog_service import build_full_tree, fetch_column_tag_details
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


@router.get("/api/access/catalog-access/{user}")
def catalog_access(user: str):
    """What this user can reach, laid out by catalog/schema/table rather than
    as a flat grant list - with each table's PII/tag columns attached, so
    it reads as 'what catalog, what table, what column' instead of raw
    privilege rows."""
    users = fetch_scim_users()
    groups = fetch_scim_groups()
    grants = fetch_grants()
    user_groups = build_user_groups(users, groups).get(user, [])

    def via_for(grantee: str) -> str | None:
        if grantee == user:
            return "Direct"
        if grantee in user_groups:
            return f"Group: {grantee}"
        return None

    catalog_grants: dict[str, tuple[str, str]] = {}
    schema_grants: dict[str, tuple[str, str]] = {}
    table_grants: dict[str, tuple[str, str]] = {}
    for g in grants:
        via = via_for(g["grantee"])
        if not via:
            continue
        bucket = {"CATALOG": catalog_grants, "SCHEMA": schema_grants, "TABLE": table_grants}.get(g["level"])
        if bucket is not None:
            bucket.setdefault(g["object"], (g["privilege"], via))

    column_tag_details = fetch_column_tag_details()

    rows = []
    for cat in build_full_tree():
        catalog_name = cat["catalog"]
        cat_hit = catalog_grants.get(catalog_name)
        catalog_has_table_row = False
        for schema in cat["schemas"]:
            schema_key = f"{catalog_name}.{schema['schema']}"
            schema_hit = schema_grants.get(schema_key)
            for obj in schema["objects"]:
                table_key = f"{schema_key}.{obj['name']}"
                hit = table_grants.get(table_key) or schema_hit or cat_hit
                if not hit:
                    continue
                privilege, via = hit
                catalog_has_table_row = True
                rows.append({
                    "catalog": catalog_name,
                    "schema": schema["schema"],
                    "table": obj["name"],
                    "kind": obj["kind"],
                    "privilege": privilege,
                    "via": via,
                    "tags": obj.get("tags", []),
                    "columns": column_tag_details.get(table_key, []),
                })
        if cat_hit and not catalog_has_table_row:
            privilege, via = cat_hit
            rows.append({
                "catalog": catalog_name, "schema": None, "table": None, "kind": "CATALOG",
                "privilege": privilege, "via": via, "tags": cat.get("tags", []), "columns": [],
            })

    rows.sort(key=lambda r: (r["catalog"], r["schema"] or "", r["table"] or ""))
    return {"user": user, "groups": user_groups, "access": rows}
