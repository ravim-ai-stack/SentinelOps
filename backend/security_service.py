"""
Data security service — PII discovery over Unity Catalog column metadata.

Column names are classified entirely by Databricks' built-in SQL AI
functions — ai_classify() picks a PII category (or "Not PII") for every
distinct column name in the workspace, and one ai_similarity() call (against
just that name's matched category's exemplar phrase, not all 7) scores it,
standing in for confidence since ai_classify() itself returns no score. No
keyword/regex matching is used — every classification decision comes from
the model.

Column names essentially never change once classified, so results are kept
in an unbounded, process-lifetime cache (_classification_cache) keyed by
name - only column names never seen before are sent to the AI functions, in
small chunks. This is what keeps repeat page loads cheap: the first load of
a fresh process classifies everything (can be slow on a large schema), every
load after that is close to a pure in-memory lookup with zero AI calls for
names already known.

ai_query() powers the one-line "why this is sensitive" summary attached to
each Sensitive tables row, batched into a single call per panel load (one
prompt covering every sensitive table) rather than one call per table.

Access-per-table reuses the same grants already computed for Access
governance.
"""

import json
import logging
import os
import re
import threading
import time
from typing import Optional

from cache import ttl_cache
from catalog_service import fetch_table_tags
from databricks_client import run_query
from grants_service import find_table_grantees, resolve_table_access, resolve_table_access_detail

security_logger = logging.getLogger("sentinelops.security")

MODEL_NAME = os.environ.get("MODEL_NAME", "").strip()

# build_pii_columns() is called independently by 6 different panels across
# Data security and Dashboard - cache briefly so one page load makes one
# Databricks round-trip instead of one per panel.
CACHE_SECONDS = 8

RISK_RANK = {"high": 3, "medium": 2, "low": 1}

PII_CATEGORY_RISK: list[tuple[str, str]] = [
    ("SSN / National ID", "high"),
    ("Credit Card Number", "high"),
    ("Health Records", "high"),
    ("Email Address", "medium"),
    ("Phone Number", "medium"),
    ("Physical Address", "medium"),
    ("Date of Birth", "low"),
]
CATEGORY_RISK = dict(PII_CATEGORY_RISK)

AI_NOT_PII_LABEL = "Not PII"
AI_LABELS = [category for category, _ in PII_CATEGORY_RISK] + [AI_NOT_PII_LABEL]

# One exemplar phrase per category, scored against the column name with
# ai_similarity() — the highest score across all of them doubles as a
# confidence signal, since ai_classify() itself returns a label with no score.
PII_EXEMPLARS: dict[str, str] = {
    "SSN / National ID": "social security number or national identification number",
    "Credit Card Number": "credit card number",
    "Health Records": "medical diagnosis or health record",
    "Email Address": "email address",
    "Phone Number": "phone number",
    "Physical Address": "home mailing address",
    "Date of Birth": "date of birth",
}

# How many never-before-seen column names go into one ai_classify/
# ai_similarity SQL call. Only applies to names not already in
# _classification_cache, so this is a batch-size limit, not a coverage
# cap — uncached names beyond one chunk just go out in the next chunk,
# nothing is silently dropped.
CLASSIFY_CHUNK_SIZE = 100

# Persistent, unbounded-TTL cache of column_name -> classification (or None
# for "not PII"/unclassifiable). Cleared only on process restart. This is
# the actual efficiency fix: without it, every 8-second panel refresh would
# re-run AI classification on every column in the workspace forever, even
# though column names essentially never change.
_classification_cache: dict[str, Optional[dict]] = {}
_classification_cache_lock = threading.Lock()


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _classify_chunk(names: list[str]) -> dict[str, Optional[dict]]:
    """One batched SQL call for names not yet in the cache: ai_classify()
    picks a category per name, then a CASE expression runs exactly one
    ai_similarity() call per row — against only that row's matched
    category's exemplar phrase, not all 7 — cutting AI calls per column
    from 8 down to 2."""
    labels_sql = ", ".join(_sql_literal(label) for label in AI_LABELS)
    values_sql = ", ".join(f"({_sql_literal(name)})" for name in names)
    similarity_case_sql = " ".join(
        f"WHEN {_sql_literal(category)} THEN ai_similarity(column_name, {_sql_literal(phrase)})"
        for category, phrase in PII_EXEMPLARS.items()
    )
    sql = f"""
        WITH classified AS (
            SELECT column_name, ai_classify(column_name, array({labels_sql})) AS category
            FROM (VALUES {values_sql}) AS t(column_name)
        )
        SELECT column_name, category,
               CASE category {similarity_case_sql} ELSE NULL END AS best_similarity
        FROM classified
    """
    try:
        rows = run_query(sql)
    except Exception:
        security_logger.warning("ai_classify/ai_similarity unavailable on this workspace", exc_info=True)
        return {name: None for name in names}

    result: dict[str, Optional[dict]] = {name: None for name in names}
    for r in rows:
        category = r.get("category")
        if not category or category == AI_NOT_PII_LABEL or category not in CATEGORY_RISK:
            continue
        similarity = r.get("best_similarity") or 0
        result[r["column_name"]] = {
            "category": category,
            "risk": CATEGORY_RISK[category],
            "confidence": max(1, min(97, round(similarity * 100))),
        }
    return result


def _ai_classify_columns(column_names: set[str]) -> dict[str, dict]:
    """Looks up each column name in the persistent classification cache
    first; only names never seen before are sent to the AI functions, in
    chunks of CLASSIFY_CHUNK_SIZE. After the first classification of a
    given name, every later call is a pure in-memory lookup — no AI call,
    no SQL round-trip."""
    if not column_names:
        return {}

    with _classification_cache_lock:
        uncached = [name for name in column_names if name not in _classification_cache]

    for i in range(0, len(uncached), CLASSIFY_CHUNK_SIZE):
        chunk = uncached[i:i + CLASSIFY_CHUNK_SIZE]
        classified = _classify_chunk(chunk)
        with _classification_cache_lock:
            _classification_cache.update(classified)

    with _classification_cache_lock:
        return {
            name: _classification_cache[name]
            for name in column_names
            if _classification_cache.get(name) is not None
        }


def fetch_all_columns() -> list[dict]:
    return run_query(
        """
        SELECT table_catalog AS catalog, table_schema AS schema, table_name AS name,
               column_name, data_type
        FROM system.information_schema.columns
        WHERE table_schema != 'information_schema'
        """
    )


def _pii_row(r: dict, match: dict) -> dict:
    return {
        "catalog": r["catalog"], "schema": r["schema"], "table": r["name"],
        "table_path": f"{r['catalog']}.{r['schema']}.{r['name']}",
        "column": r["column_name"], "data_type": r["data_type"],
        "category": match["category"], "risk": match["risk"], "confidence": match["confidence"],
    }


@ttl_cache(CACHE_SECONDS)
def build_pii_columns() -> list[dict]:
    columns = fetch_all_columns()
    ai_matches = _ai_classify_columns({r["column_name"] for r in columns})
    results = []
    for r in columns:
        match = ai_matches.get(r["column_name"])
        if match:
            results.append(_pii_row(r, match))
    return results


def group_pii_by_table(pii_columns: list[dict]) -> dict[str, list[dict]]:
    by_table: dict[str, list[dict]] = {}
    for c in pii_columns:
        by_table.setdefault(c["table_path"], []).append(c)
    return by_table


def compute_table_risk(cols: list[dict]) -> str:
    return max((c["risk"] for c in cols), key=lambda r: RISK_RANK[r])


# ---------------------------------------------------------------------------
# ai_query() — batched "why this is sensitive" narrative for Sensitive tables
# ---------------------------------------------------------------------------

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)
_SUMMARY_CACHE_SECONDS = CACHE_SECONDS
_summary_cache: dict[tuple, tuple[float, dict]] = {}
_summary_cache_lock = threading.Lock()


def _build_table_summary_prompt(tables: list[dict]) -> str:
    lines = [
        "You are a data governance assistant. For each table below, write ONE short "
        "sentence (max 25 words) explaining why it is sensitive, referencing its PII "
        "categories and who can access it. Respond with ONLY a JSON object mapping each "
        "table's exact name to its one-sentence summary - no text outside the JSON object.",
        "",
    ]
    for t in tables:
        lines.append(
            f"- {t['table']}: risk={t['risk']}, pii_columns={t['pii_columns']}, "
            f"categories={t['data_types']}, access={t['access']}"
        )
    return "\n".join(lines)


def generate_sensitive_table_summaries(tables: list[dict]) -> dict[str, str]:
    """One batched ai_query() call covering every sensitive table on the
    panel, rather than one call per table, so this stays affordable even
    though it runs on every panel load. Cached the same short window as
    build_pii_columns(); degrades to no summaries if MODEL_NAME isn't
    configured or the call fails - the panel still works without them."""
    if not MODEL_NAME or not tables:
        return {}

    key = tuple(sorted((t["table"], t["risk"]) for t in tables))
    now = time.monotonic()
    with _summary_cache_lock:
        hit = _summary_cache.get(key)
        if hit is not None and now - hit[0] < _SUMMARY_CACHE_SECONDS:
            return hit[1]

    prompt = _build_table_summary_prompt(tables)
    sql = f"""
        SELECT ai_query(
            '{MODEL_NAME}',
            :prompt,
            modelParameters => named_struct('max_tokens', 800, 'temperature', 0.2)
        ) AS response
    """
    try:
        rows = run_query(sql, {"prompt": prompt})
        content = rows[0]["response"]
        cleaned = _JSON_FENCE_RE.sub("", content.strip())
        parsed = json.loads(cleaned)
        summaries = {k: str(v) for k, v in parsed.items() if isinstance(v, str)}
    except Exception:
        security_logger.warning("ai_query table-summary generation failed", exc_info=True)
        summaries = {}

    with _summary_cache_lock:
        _summary_cache[key] = (now, summaries)
    return summaries


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

    summaries = generate_sensitive_table_summaries(tables)
    for t in tables:
        t["ai_summary"] = summaries.get(t["table"])
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
