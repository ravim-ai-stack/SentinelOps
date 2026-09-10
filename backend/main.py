"""
SentinelOps backend - Catalog Explorer API

Exposes Unity Catalog metadata read live from a Databricks SQL Warehouse
(system.information_schema) plus the Unity Catalog REST API (for registered
models), so the Catalog Explorer tab in ui.html can browse
catalog -> schema -> {tables, views, functions, volumes, models} with
server-side search, instead of showing mock rows.

Run:
    pip install -r requirements.txt 
    copy .env.example .env      # fill in DATABRICKS_HOST / TOKEN / WAREHOUSE_ID
    python main.py               # starts the API and opens ui.html
    # or, for auto-reload during development:
    uvicorn main:app --reload --port 3001
"""

import logging
import re
from typing import Optional

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from databricks_client import rest_get_all, run_query, scim_get_all

logger = logging.getLogger("sentinelops.catalog")
access_logger = logging.getLogger("sentinelops.access")
security_logger = logging.getLogger("sentinelops.security")

app = FastAPI(title="SentinelOps Catalog Explorer API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

EXCLUDED_SCHEMAS = {"information_schema"}


@app.get("/api/health")
def health():
    return {"status": "ok"}


def fetch_catalogs() -> dict[str, dict]:
    rows = run_query(
        """
        SELECT catalog_name, catalog_owner, comment, last_altered
        FROM system.information_schema.catalogs
        """
    )
    return {r["catalog_name"]: r for r in rows}


def fetch_schemata() -> dict[str, dict]:
    rows = run_query(
        """
        SELECT catalog_name, schema_name, schema_owner, comment, last_altered
        FROM system.information_schema.schemata
        WHERE schema_name != 'information_schema'
        """
    )
    return {f"{r['catalog_name']}.{r['schema_name']}": r for r in rows}


def fetch_tables_and_views() -> list[dict]:
    rows = run_query(
        """
        SELECT table_catalog AS catalog, table_schema AS schema, table_name AS name,
               table_type, table_owner AS owner, last_altered
        FROM system.information_schema.tables
        WHERE table_schema != 'information_schema'
        """
    )
    out = []
    for r in rows:
        out.append({
            "catalog": r["catalog"],
            "schema": r["schema"],
            "name": r["name"],
            "kind": "VIEW" if r["table_type"] == "VIEW" else "TABLE",
            "type": r["table_type"],
            "owner": r["owner"],
            "last_altered": r["last_altered"],
        })
    return out


def fetch_functions() -> list[dict]:
    rows = run_query(
        """
        SELECT routine_catalog AS catalog, routine_schema AS schema, routine_name AS name,
               routine_owner AS owner, routine_type, last_altered
        FROM system.information_schema.routines
        WHERE routine_schema != 'information_schema'
        """
    )
    return [
        {
            "catalog": r["catalog"],
            "schema": r["schema"],
            "name": r["name"],
            "kind": "FUNCTION",
            "type": r["routine_type"] or "FUNCTION",
            "owner": r["owner"],
            "last_altered": r["last_altered"],
        }
        for r in rows
    ]


def fetch_volumes() -> list[dict]:
    try:
        rows = run_query(
            """
            SELECT volume_catalog AS catalog, volume_schema AS schema, volume_name AS name,
                   volume_type, volume_owner AS owner, last_altered
            FROM system.information_schema.volumes
            WHERE volume_schema != 'information_schema'
            """
        )
    except Exception:
        logger.warning("Volumes not available on this workspace", exc_info=True)
        return []
    return [
        {
            "catalog": r["catalog"],
            "schema": r["schema"],
            "name": r["name"],
            "kind": "VOLUME",
            "type": r["volume_type"],
            "owner": r["owner"],
            "last_altered": r["last_altered"],
        }
        for r in rows
    ]


def fetch_models() -> list[dict]:
    """Unity Catalog registered models — via REST since information_schema
    has no models table. Skipped gracefully if UC model registry isn't
    enabled/accessible for this token."""
    try:
        models = rest_get_all("/api/2.1/unity-catalog/models", "registered_models")
    except Exception:
        logger.warning("Registered models not available", exc_info=True)
        return []
    return [
        {
            "catalog": m["catalog_name"],
            "schema": m["schema_name"],
            "name": m["name"],
            "kind": "MODEL",
            "type": m.get("securable_kind", "MODEL"),
            "owner": m.get("owner"),
            "last_altered": m.get("updated_at"),  # epoch millis
        }
        for m in models
    ]


# ---------------------------------------------------------------------------
# Tree assembly + search
# ---------------------------------------------------------------------------

def build_full_tree() -> list[dict]:
    catalogs = fetch_catalogs()
    schemata = fetch_schemata()
    objects = fetch_tables_and_views() + fetch_functions() + fetch_volumes() + fetch_models()

    schema_buckets: dict[str, dict] = {}
    for key, s in schemata.items():
        schema_buckets[key] = {
            "schema": s["schema_name"],
            "owner": s["schema_owner"],
            "last_altered": s["last_altered"],
            "objects": [],
        }

    for obj in objects:
        key = f"{obj['catalog']}.{obj['schema']}"
        bucket = schema_buckets.setdefault(key, {
            "schema": obj["schema"], "owner": None, "last_altered": None, "objects": [],
        })
        bucket["objects"].append({
            "name": obj["name"],
            "kind": obj["kind"],
            "type": obj["type"],
            "owner": obj["owner"],
            "last_altered": obj["last_altered"],
        })

    catalog_buckets: dict[str, dict] = {}
    for key, bucket in schema_buckets.items():
        catalog_name = key.split(".", 1)[0]
        cat = catalog_buckets.setdefault(catalog_name, {
            "catalog": catalog_name,
            "owner": catalogs.get(catalog_name, {}).get("catalog_owner"),
            "schemas": [],
        })
        cat["schemas"].append(bucket)

    for catalog_name, cat_info in catalogs.items():
        catalog_buckets.setdefault(catalog_name, {
            "catalog": catalog_name,
            "owner": cat_info.get("catalog_owner"),
            "schemas": [],
        })

    tree = list(catalog_buckets.values())
    for cat in tree:
        cat["schemas"].sort(key=lambda s: s["schema"])
        for s in cat["schemas"]:
            s["objects"].sort(key=lambda o: (o["kind"], o["name"]))
    tree.sort(key=lambda c: c["catalog"])
    return tree


def _match(name: Optional[str], needle: str) -> bool:
    return bool(name) and needle in name.lower()


def filter_tree(tree: list[dict], search: Optional[str]) -> list[dict]:
    if not search:
        return tree
    needle = search.strip().lower()
    if not needle:
        return tree

    filtered = []
    for cat in tree:
        catalog_match = _match(cat["catalog"], needle)
        kept_schemas = []
        for schema in cat["schemas"]:
            schema_match = _match(schema["schema"], needle)
            if catalog_match or schema_match:
                kept_objects = schema["objects"]
            else:
                kept_objects = [o for o in schema["objects"] if _match(o["name"], needle)]
            if kept_objects or catalog_match or schema_match:
                kept_schemas.append({**schema, "objects": kept_objects})
        if kept_schemas or catalog_match:
            filtered.append({**cat, "schemas": kept_schemas})
    return filtered


def summarize(tree: list[dict]) -> dict:
    schema_count = sum(len(c["schemas"]) for c in tree)
    objects = [o for c in tree for s in c["schemas"] for o in s["objects"]]
    return {
        "catalogs": len(tree),
        "schemas": schema_count,
        "tables": sum(1 for o in objects if o["kind"] in ("TABLE", "VIEW")),
        "functions": sum(1 for o in objects if o["kind"] == "FUNCTION"),
        "volumes": sum(1 for o in objects if o["kind"] == "VOLUME"),
        "models": sum(1 for o in objects if o["kind"] == "MODEL"),
        "objects": len(objects),
    }


@app.get("/api/catalog/tree")
def catalog_tree(search: Optional[str] = Query(None, description="Filter by catalog/schema/object name")):
    tree = build_full_tree()
    filtered = filter_tree(tree, search)
    return {"summary": summarize(filtered), "catalogs": filtered}


@app.get("/api/catalog/{catalog}/access")
def catalog_access(catalog: str):
    """Who can reach a catalog — split into group access (with each group's
    member list, for the Catalog Explorer's 'View access' drill-down) and
    direct user access. Reuses the same grant-resolution helpers as the
    Data security page's sensitive-table access breakdown."""
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


# ---------------------------------------------------------------------------
# Access governance — SCIM users/groups + Unity Catalog privilege grants
#
# There's no "access log" queried here (that would need system.access.audit,
# which isn't enabled on every workspace) — "most active" and "high risk" are
# derived instead from how many securable objects a user can reach today,
# directly or through group membership, per Unity Catalog's own grants.
# ---------------------------------------------------------------------------

SENSITIVE_KEYWORDS = {"pii", "security", "hr", "finance", "healthcare", "confidential", "payroll", "secret"}
BROAD_PRIVILEGES = {"ALL_PRIVILEGES", "MODIFY"}
LARGE_OBJECT_COUNT = 10


def fetch_scim_users() -> list[dict]:
    return scim_get_all("/api/2.0/preview/scim/v2/Users")


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
            """
            SELECT grantee, volume_catalog, volume_schema, volume_name, privilege_type
            FROM system.information_schema.volume_privileges
            """
        )
    except Exception:
        access_logger.warning("Volume privileges not available on this workspace", exc_info=True)
        volume_rows = []

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


def compute_user_access(users: list[dict], user_groups: dict[str, list[str]], grants: list[dict]) -> list[dict]:
    grants_by_grantee: dict[str, list[dict]] = {}
    for g in grants:
        grants_by_grantee.setdefault(g["grantee"], []).append(g)

    rows: list[dict] = []
    for u in users:
        uname = u.get("userName")
        if not uname:
            continue
        groups = user_groups.get(uname, [])
        effective = list(grants_by_grantee.get(uname, []))
        for gname in groups:
            effective.extend(grants_by_grantee.get(gname, []))

        objects = {g["object"] for g in effective}
        broad_hit = any(_is_broad(g["level"], g["privilege"]) for g in effective)
        sensitive_hit = any(_is_sensitive(g["object"]) for g in effective)
        risky_objects = {
            g["object"] for g in effective
            if _is_broad(g["level"], g["privilege"]) or _is_sensitive(g["object"])
        }

        if broad_hit and sensitive_hit:
            risk_level, reason = "high", "Broad access to sensitive schemas/catalogs"
        elif sensitive_hit:
            risk_level, reason = "high", "Access to sensitive schemas or tables"
        elif broad_hit:
            risk_level, reason = "high", "Broad catalog/schema-level privileges"
        elif len(objects) >= LARGE_OBJECT_COUNT:
            risk_level, reason = "medium", f"Access to a large number of objects ({len(objects)})"
        else:
            risk_level, reason = None, None

        rows.append({
            "user": uname,
            "display_name": u.get("displayName") or uname,
            "groups": groups,
            "principal_type": "User",
            "active": u.get("active", True),
            "objects_with_access": len(objects),
            "high_risk_objects": len(risky_objects),
            "risk_level": risk_level,
            "risk_reason": reason,
        })
    return rows


def compute_group_summaries(groups: list[dict], grants: list[dict]) -> list[dict]:
    """Access granted directly to each group (members inherit these grants
    on top of whatever is granted to them individually)."""
    grants_by_grantee: dict[str, list[dict]] = {}
    for g in grants:
        grants_by_grantee.setdefault(g["grantee"], []).append(g)

    rows = []
    for g in groups:
        gname = g.get("displayName", "")
        effective = grants_by_grantee.get(gname, [])
        objects = {gr["object"] for gr in effective}
        risky_objects = {
            gr["object"] for gr in effective
            if _is_broad(gr["level"], gr["privilege"]) or _is_sensitive(gr["object"])
        }
        rows.append({
            "group": gname,
            "member_count": len(g.get("members", [])),
            "objects_with_access": len(objects),
            "high_risk_objects": len(risky_objects),
        })
    rows.sort(key=lambda r: r["objects_with_access"], reverse=True)
    return rows


@app.get("/api/access/overview")
def access_overview():
    users = fetch_scim_users()
    groups = fetch_scim_groups()
    user_groups = build_user_groups(users, groups)
    grants = fetch_grants()

    user_access = compute_user_access(users, user_groups, grants)
    user_access.sort(key=lambda u: u["objects_with_access"], reverse=True)

    most_active = [u for u in user_access if u["objects_with_access"] > 0][:5]
    high_risk = sorted(
        (u for u in user_access if u["risk_level"]),
        key=lambda u: (u["risk_level"] != "high", -u["high_risk_objects"]),
    )
    group_access = compute_group_summaries(groups, grants)

    return {
        "summary": {
            "total_users": len(users),
            "total_groups": len(groups),
            "total_grants": len(grants),
            "high_risk_users": len(high_risk),
        },
        "users": user_access,
        "most_active": most_active,
        "high_risk": high_risk,
        "groups": group_access,
        "grants": grants,
    }


# ---------------------------------------------------------------------------
# Data security — PII discovery over Unity Catalog column metadata
#
# There's no data-scanning/profiling job here (that would mean reading actual
# row values) — sensitivity is inferred from column *names* against a keyword
# list per category, which is what's realistically available from
# information_schema without an external classifier. Confidence is a rough
# heuristic (longer/more specific keyword match = higher), not a statistical
# score, and access-per-table reuses the same grants already computed for
# Access governance.
# ---------------------------------------------------------------------------

RISK_RANK = {"high": 3, "medium": 2, "low": 1}

PII_CATEGORIES: list[tuple[str, str, list[str]]] = [
    ("SSN / National ID", "high", [r"\bssn\b", r"social_security", r"national_id", r"aadhaar|aadhar", r"passport_number"]),
    ("Credit Card Number", "high", [r"credit_card", r"card_number", r"card_last4", r"\bcvv\b", r"\bcc_number\b"]),
    ("Health Records", "high", [r"diagnosis", r"medical_record", r"health_record", r"\bicd_?code\b", r"patient_id"]),
    ("Email Address", "medium", [r"e[-_]?mail"]),
    ("Phone Number", "medium", [r"phone", r"mobile_number", r"contact_number"]),
    ("Physical Address", "medium", [r"street_address", r"postal_code", r"\bzip(code)?\b", r"\baddress\b"]),
    ("Date of Birth", "low", [r"date_of_birth", r"\bdob\b", r"birth_date"]),
]


def classify_column(column_name: str) -> Optional[dict]:
    name = column_name.lower()
    for category, risk, patterns in PII_CATEGORIES:
        for pat in patterns:
            if re.search(pat, name):
                specificity = len(re.sub(r"[\\bB()|_-]", "", pat))
                confidence = min(98, 78 + specificity * 2)
                return {"category": category, "risk": risk, "confidence": confidence}
    return None


def fetch_all_columns() -> list[dict]:
    return run_query(
        """
        SELECT table_catalog AS catalog, table_schema AS schema, table_name AS name,
               column_name, data_type
        FROM system.information_schema.columns
        WHERE table_schema != 'information_schema'
        """
    )


def build_pii_columns() -> list[dict]:
    columns = fetch_all_columns()
    results = []
    for r in columns:
        match = classify_column(r["column_name"])
        if not match:
            continue
        results.append({
            "catalog": r["catalog"], "schema": r["schema"], "table": r["name"],
            "table_path": f"{r['catalog']}.{r['schema']}.{r['name']}",
            "column": r["column_name"], "data_type": r["data_type"],
            "category": match["category"], "risk": match["risk"], "confidence": match["confidence"],
        })
    return results


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


def build_sensitive_tables(
    pii_columns: list[dict],
    table_meta: dict[str, dict],
    grants: list[dict],
    group_member_count: dict[str, int],
    group_members: dict[str, list[str]],
) -> list[dict]:
    by_table: dict[str, list[dict]] = {}
    for c in pii_columns:
        by_table.setdefault(c["table_path"], []).append(c)

    tables = []
    for table_path, cols in by_table.items():
        catalog, schema, _name = table_path.split(".", 2)
        risk = max((c["risk"] for c in cols), key=lambda r: RISK_RANK[r])
        meta = table_meta.get(table_path, {})
        grantees = find_table_grantees(catalog, schema, table_path, grants)
        tables.append({
            "table": table_path,
            "pii_columns": [c["column"] for c in cols],
            "data_types": sorted({c["category"] for c in cols}),
            "risk": risk,
            "access": resolve_table_access(grantees, group_member_count),
            "access_detail": resolve_table_access_detail(grantees, group_member_count, group_members),
            "last_altered": meta.get("last_altered"),
            "owner": meta.get("owner"),
        })
    tables.sort(key=lambda t: (RISK_RANK[t["risk"]], len(t["pii_columns"])), reverse=True)
    return tables


def summarize_pii(pii_columns: list[dict], sensitive_tables: list[dict]) -> dict:
    risk_counts = {"high": 0, "medium": 0, "low": 0}
    category_counts: dict[str, int] = {}
    for c in pii_columns:
        risk_counts[c["risk"]] += 1
        category_counts[c["category"]] = category_counts.get(c["category"], 0) + 1

    top_tables = sorted(sensitive_tables, key=lambda t: len(t["pii_columns"]), reverse=True)[:5]
    return {
        "tables_with_pii": len(sensitive_tables),
        "high_risk_tables": sum(1 for t in sensitive_tables if t["risk"] == "high"),
        "pii_columns": len(pii_columns),
        "classified_datasets": len(sensitive_tables),
        "risk_breakdown": risk_counts,
        "category_breakdown": category_counts,
        "top_tables": [{"table": t["table"], "count": len(t["pii_columns"])} for t in top_tables],
    }


@app.get("/api/security/overview")
def security_overview():
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

    sensitive_tables = build_sensitive_tables(pii_columns, table_meta, grants, group_member_count, group_members)
    summary = summarize_pii(pii_columns, sensitive_tables)

    recent_classifications = sorted(
        pii_columns, key=lambda c: table_meta.get(c["table_path"], {}).get("last_altered") or "", reverse=True
    )[:8]
    for c in recent_classifications:
        c["last_altered"] = table_meta.get(c["table_path"], {}).get("last_altered")

    return {
        "summary": summary,
        "sensitive_tables": sensitive_tables,
        "recent_classifications": recent_classifications,
    }


if __name__ == "__main__":
    import threading
    import webbrowser
    from pathlib import Path

    import uvicorn

    HOST, PORT = "127.0.0.1", 3001
    ui_path = Path(__file__).resolve().parent.parent / "ui.html"

    def _open_ui():
        webbrowser.open(ui_path.as_uri())

    threading.Timer(1.5, _open_ui).start()
    uvicorn.run(app, host=HOST, port=PORT)
