"""Data security page — the top 4 stat cards."""

from fastapi import APIRouter

from security_service import build_pii_columns, count_tables_with_pii

router = APIRouter()


@router.get("/api/security/stats")
def security_stats():
    pii_columns = build_pii_columns()
    table_counts = count_tables_with_pii(pii_columns)
    return {
        "tables_with_pii": table_counts["tables_with_pii"],
        "high_risk_tables": table_counts["high_risk_tables"],
        "pii_columns": len(pii_columns),
        "classified_datasets": table_counts["tables_with_pii"],
    }
