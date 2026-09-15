"""
Access governance service — risk scoring for users and groups, derived from
how many securable objects each principal can reach today (directly or via
group membership), per Unity Catalog's own grants.

There's no "access log" queried here (that would need system.access.audit,
which isn't enabled on every workspace) — "most active" and "high risk" are
derived instead from current grant reach, not historical usage.
"""

from grants_service import _is_broad, _is_sensitive

LARGE_OBJECT_COUNT = 10


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
