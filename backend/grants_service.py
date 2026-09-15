"""
Shared SCIM (users/groups) and Unity Catalog grant primitives used by both
the Access governance and Data security services — resolving who can reach
a given catalog/schema/table, and whether a grant counts as broad or
sensitive.
"""

import logging
<<<<<<< HEAD
from concurrent.futures import ThreadPoolExecutor
=======
>>>>>>> c0a15a48c04ca4d88d9e2778bb6e0ad3f03bae2c

from cache import ttl_cache
from databricks_client import run_query, scim_get_all

access_logger = logging.getLogger("sentinelops.access")

SENSITIVE_KEYWORDS = {"pii", "security", "hr", "finance", "healthcare", "confidential", "payroll", "secret"}
BROAD_PRIVILEGES = {"ALL_PRIVILEGES", "MODIFY"}

# Every panel on Access governance (and some on Data security) needs
<<<<<<< HEAD
# SCIM users/groups and grants independently - cache them so repeated
# panel loads, tab switches and filter changes within a live-refresh cycle
# (30s on the frontend) reuse one Databricks round-trip per fetcher instead
# of re-querying the warehouse on every interaction.
CACHE_SECONDS = 30
=======
# SCIM users/groups and grants independently - cache them briefly so a
# single page load collapses to one Databricks round-trip per fetcher
# instead of one per panel.
CACHE_SECONDS = 8
>>>>>>> c0a15a48c04ca4d88d9e2778bb6e0ad3f03bae2c


@ttl_cache(CACHE_SECONDS)
def fetch_scim_users() -> list[dict]:
    return scim_get_all("/api/2.0/preview/scim/v2/Users")


@ttl_cache(CACHE_SECONDS)
def fetch_scim_groups() -> list[dict]:
    return scim_get_all("/api/2.0/preview/scim/v2/Groups")


def build_user_groups(users: list[dict], groups: list[dict]) -> dict[str, list[str]]:
    """Map userName -> sorted list of group display names, preferring the
    Groups endpoint's member list (source of truth) and falling back to the
    'groups' field SCIM sometimes returns on the user resource itself."""
    member_id_to_groups: dict[str, list[str]] = {}
    for g in groups:
        gname = g.get("displayName", "")
        for m in g.get("members", []):
            member_id_to_groups.setdefault(m.get("value"), []).append(gname)

    result: dict[str, list[str]] = {}
    for u in users:
        uname = u.get("userName")
        if not uname:
            continue
        via_groups_endpoint = member_id_to_groups.get(u.get("id"), [])
        via_user_field = [g.get("display") for g in u.get("groups", []) if g.get("display")]
        result[uname] = sorted(set(via_groups_endpoint or via_user_field))
    return result


def build_group_members(users: list[dict], groups: list[dict]) -> dict[str, list[str]]:
    """Group displayName -> sorted list of member userNames (emails), resolved
    from SCIM group membership (member ids) against the SCIM users list."""
    id_to_username = {u.get("id"): u.get("userName") for u in users if u.get("id")}
    result: dict[str, list[str]] = {}
    for g in groups:
        gname = g.get("displayName", "")
        members = []
        for m in g.get("members", []):
            uname = id_to_username.get(m.get("value")) or m.get("display")
            if uname:
                members.append(uname)
        result[gname] = sorted(set(members))
    return result


<<<<<<< HEAD
def _fetch_volume_privileges() -> list[dict]:
    try:
        return run_query(
=======
@ttl_cache(CACHE_SECONDS)
def fetch_grants() -> list[dict]:
    """Flatten catalog/schema/table/volume privilege grants into one list of
    {grantee, level, object, privilege}. `grantee` is a principal name — a
    user email or a group name, straight from Unity Catalog's own grants."""
    catalog_rows = run_query(
        "SELECT grantee, catalog_name, privilege_type FROM system.information_schema.catalog_privileges"
    )
    schema_rows = run_query(
        """
        SELECT grantee, catalog_name, schema_name, privilege_type
        FROM system.information_schema.schema_privileges
        """
    )
    table_rows = run_query(
        """
        SELECT grantee, table_catalog, table_schema, table_name, privilege_type
        FROM system.information_schema.table_privileges
        """
    )
    try:
        volume_rows = run_query(
>>>>>>> c0a15a48c04ca4d88d9e2778bb6e0ad3f03bae2c
            """
            SELECT grantee, volume_catalog, volume_schema, volume_name, privilege_type
            FROM system.information_schema.volume_privileges
            """
        )
    except Exception:
        access_logger.warning("Volume privileges not available on this workspace", exc_info=True)
<<<<<<< HEAD
        return []


@ttl_cache(CACHE_SECONDS)
def fetch_grants() -> list[dict]:
    """Flatten catalog/schema/table/volume privilege grants into one list of
    {grantee, level, object, privilege}. `grantee` is a principal name — a
    user email or a group name, straight from Unity Catalog's own grants.
    Each of these is its own Databricks round-trip (its own SQL connection),
    so they're run concurrently rather than one after another."""
    with ThreadPoolExecutor(max_workers=4) as pool:
        catalog_f = pool.submit(
            run_query, "SELECT grantee, catalog_name, privilege_type FROM system.information_schema.catalog_privileges"
        )
        schema_f = pool.submit(
            run_query,
            """
            SELECT grantee, catalog_name, schema_name, privilege_type
            FROM system.information_schema.schema_privileges
            """,
        )
        table_f = pool.submit(
            run_query,
            """
            SELECT grantee, table_catalog, table_schema, table_name, privilege_type
            FROM system.information_schema.table_privileges
            """,
        )
        volume_f = pool.submit(_fetch_volume_privileges)

        catalog_rows = catalog_f.result()
        schema_rows = schema_f.result()
        table_rows = table_f.result()
        volume_rows = volume_f.result()
=======
        volume_rows = []
>>>>>>> c0a15a48c04ca4d88d9e2778bb6e0ad3f03bae2c

    grants: list[dict] = []
    for r in catalog_rows:
        grants.append({
            "grantee": r["grantee"], "level": "CATALOG",
            "object": r["catalog_name"], "privilege": r["privilege_type"],
        })
    for r in schema_rows:
        grants.append({
            "grantee": r["grantee"], "level": "SCHEMA",
            "object": f"{r['catalog_name']}.{r['schema_name']}", "privilege": r["privilege_type"],
        })
    for r in table_rows:
        grants.append({
            "grantee": r["grantee"], "level": "TABLE",
            "object": f"{r['table_catalog']}.{r['table_schema']}.{r['table_name']}",
            "privilege": r["privilege_type"],
        })
    for r in volume_rows:
        grants.append({
            "grantee": r["grantee"], "level": "VOLUME",
            "object": f"{r['volume_catalog']}.{r['volume_schema']}.{r['volume_name']}",
            "privilege": r["privilege_type"],
        })
    return grants


def _is_sensitive(object_path: str) -> bool:
    lowered = object_path.lower()
    return any(kw in lowered for kw in SENSITIVE_KEYWORDS)


def _is_broad(level: str, privilege: str) -> bool:
    priv = (privilege or "").upper()
    if priv in BROAD_PRIVILEGES:
        return True
    return level == "CATALOG" and priv not in {"USE_CATALOG", "USE_SCHEMA"}


def find_table_grantees(catalog: str, schema: str, table_path: str, grants: list[dict]) -> set[str]:
    grantees = set()
    for g in grants:
        if (
            (g["level"] == "CATALOG" and g["object"] == catalog)
            or (g["level"] == "SCHEMA" and g["object"] == f"{catalog}.{schema}")
            or (g["level"] == "TABLE" and g["object"] == table_path)
        ):
            grantees.add(g["grantee"])
    return grantees


def resolve_table_access(grantees: set[str], group_member_count: dict[str, int]) -> str:
    if not grantees:
        return "No direct grants"
    group_hits = [(g, group_member_count[g]) for g in grantees if g in group_member_count]
    if group_hits:
        top_group, member_count = max(group_hits, key=lambda x: x[1])
        return f"{top_group} ({member_count})"
    return f"{len(grantees)} user{'s' if len(grantees) != 1 else ''}"


def resolve_table_access_detail(
    grantees: set[str], group_member_count: dict[str, int], group_members: dict[str, list[str]]
) -> dict:
    """Split a table's grantees into group access (with each group's member
    list, for the 'person view' drill-down) and direct user access."""
    groups = sorted(
        (
            {"group": g, "member_count": group_member_count[g], "members": group_members.get(g, [])}
            for g in grantees
            if g in group_member_count
        ),
        key=lambda x: x["member_count"],
        reverse=True,
    )
    users = sorted(g for g in grantees if g not in group_member_count)
    return {"groups": groups, "users": users}
