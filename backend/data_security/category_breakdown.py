"""Data security page — 'PII columns by category' bars."""

from fastapi import APIRouter

from security_service import build_pii_columns, compute_category_breakdown

router = APIRouter()


@router.get("/api/security/category-breakdown")
def category_breakdown():
    return {"category_breakdown": compute_category_breakdown(build_pii_columns())}
