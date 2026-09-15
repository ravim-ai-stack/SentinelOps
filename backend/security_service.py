"""
Data security service — PII discovery over Unity Catalog column metadata.

There's no data-scanning/profiling job here (that would mean reading actual
row values) — sensitivity is inferred from column *names* against a keyword
list per category, which is what's realistically available from
information_schema without an external classifier. Confidence is a rough
heuristic (longer/more specific keyword match = higher), not a statistical
score, and access-per-table reuses the same grants already computed for
Access governance.
"""

import re
from typing import Optional

from cache import ttl_cache
from catalog_service import fetch_table_tags
from databricks_client import run_query
from grants_service import find_table_grantees, resolve_table_access, resolve_table_access_detail

# build_pii_columns() is called independently by 6 different panels across
# Data security and Dashboard - cache briefly so one page load makes one
# Databricks round-trip instead of one per panel.
CACHE_SECONDS = 8

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


@ttl_cache(CACHE_SECONDS)
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


def group_pii_by_table(pii_columns: list[dict]) -> dict[str, list[dict]]:
    by_table: dict[str, list[dict]] = {}
    for c in pii_columns:
        by_table.setdefault(c["table_path"], []).append(c)
    return by_table


def compute_table_risk(cols: list[dict]) -> str:
    return max((c["risk"] for c in cols), key=lambda r: RISK_RANK[r])


def build_sensitive_tables(
    pii_columns: list[dict],
    table_meta: dict[str, dict],
    grants: list[dict],
    group_member_count: dict[str, int],
    group_members: dict[str, list[str]],
) -> list[dict]:
    """Full per-table breakdown, including who has access — used by the
    Sensitive tables panel, which is the only one that needs grants."""
    table_tags = fetch_table_tags()
    tables = []
    for table_path, cols in group_pii_by_table(pii_columns).items():
        catalog, schema, _name = table_path.split(".", 2)
        meta = table_meta.get(table_path, {})
        grantees = find_table_grantees(catalog, schema, table_path, grants)
        tables.append({
            "table": table_path,
            "pii_columns": [c["column"] for c in cols],
            "data_types": sorted({c["category"] for c in cols}),
            "tags": table_tags.get(table_path, []),
            "risk": compute_table_risk(cols),
            "access": resolve_table_access(grantees, group_member_count),
            "access_detail": resolve_table_access_detail(grantees, group_member_count, group_members),
            "last_altered": meta.get("last_altered"),
            "owner": meta.get("owner"),
        })
    tables.sort(key=lambda t: (RISK_RANK[t["risk"]], len(t["pii_columns"])), reverse=True)
    return tables


def count_tables_with_pii(pii_columns: list[dict]) -> dict:
    """Table-level counts for the stats panel — no grants needed."""
    by_table = group_pii_by_table(pii_columns)
    high_risk = sum(1 for cols in by_table.values() if compute_table_risk(cols) == "high")
    return {"tables_with_pii": len(by_table), "high_risk_tables": high_risk}


def compute_risk_breakdown(pii_columns: list[dict]) -> dict:
    risk_counts = {"high": 0, "medium": 0, "low": 0}
    for c in pii_columns:
        risk_counts[c["risk"]] += 1
    return risk_counts


def compute_category_breakdown(pii_columns: list[dict]) -> dict:
    category_counts: dict[str, int] = {}
    for c in pii_columns:
        category_counts[c["category"]] = category_counts.get(c["category"], 0) + 1
    return category_counts


def compute_top_pii_tables(pii_columns: list[dict], limit: int = 5) -> list[dict]:
    tables = [
        {"table": path, "count": len(cols)}
        for path, cols in group_pii_by_table(pii_columns).items()
    ]
    tables.sort(key=lambda t: t["count"], reverse=True)
    return tables[:limit]
