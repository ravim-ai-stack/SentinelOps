"""Access governance page — Effective access tab (per-user grant resolution)."""

from fastapi import APIRouter

from catalog_service import build_full_tree, fetch_column_tag_details
from grants_service import build_group_members, build_user_groups, fetch_grants, fetch_scim_groups, fetch_scim_users

router = APIRouter()


def resolve_user_catalog_access(user: str, user_groups: list[str], only_catalog: str | None = None) -> list[dict]:
    """What `user` can reach, laid out by catalog/schema/table rather than as
    a flat grant list - with each table's PII/tag columns attached, so it
    reads as 'what catalog, what table, what column' instead of raw
    privilege rows. Pass `only_catalog` to skip building rows for every
    other catalog when the caller only cares about one."""
    grants = fetch_grants()

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
        if only_catalog and catalog_name != only_catalog:
            continue
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
    return rows


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
    user_groups = build_user_groups(users, groups).get(user, [])
    rows = resolve_user_catalog_access(user, user_groups)
    return {"user": user, "groups": user_groups, "access": rows}


@router.get("/api/access/catalog-inspect/{catalog}")
def catalog_inspect(catalog: str):
    """The inverse of catalog-access: for one catalog (opened from the
    'Inspect' button in Catalog explorer), every user who can reach it -
    direct grantees plus members of any group granted access - and what
    each of them can see in it, down to PII-tagged columns."""
    users_scim = fetch_scim_users()
    groups_scim = fetch_scim_groups()
    grants = fetch_grants()
    group_members = build_group_members(users_scim, groups_scim)
    group_names = set(group_members)
    user_names = {u.get("userName") for u in users_scim if u.get("userName")}
    user_groups_map = build_user_groups(users_scim, groups_scim)

    def touches_catalog(g: dict) -> bool:
        if g["level"] == "CATALOG":
            return g["object"] == catalog
        return g["object"] == catalog or g["object"].startswith(f"{catalog}.")

    relevant_grantees = {g["grantee"] for g in grants if touches_catalog(g)}
    impacted_users = set()
    for grantee in relevant_grantees:
        if grantee in user_names:
            impacted_users.add(grantee)
        elif grantee in group_names:
            impacted_users.update(group_members.get(grantee, []))

    result_users = []
    for user in impacted_users:
        access_rows = resolve_user_catalog_access(user, user_groups_map.get(user, []), only_catalog=catalog)
        if not access_rows:
            continue
        result_users.append({"user": user, "groups": user_groups_map.get(user, []), "access": access_rows})
    result_users.sort(key=lambda u: u["user"])

    return {"catalog": catalog, "users": result_users}
