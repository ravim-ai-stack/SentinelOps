"""Dashboard page — 'Data Security & PII' mini panel (count + category
breakdown). Reuses Data security's own PII service — this is the same
underlying data, just the summarized dashboard view of it."""

from fastapi import APIRouter

from security_service import build_pii_columns, compute_category_breakdown

router = APIRouter()


@router.get("/api/dashboard/pii-overview")
def pii_overview():
    pii_columns = build_pii_columns()
    return {
        "pii_columns": len(pii_columns),
        "category_breakdown": compute_category_breakdown(pii_columns),
    }
