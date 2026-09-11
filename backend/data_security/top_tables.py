"""Data security page — 'Top tables with PII' bars."""

from fastapi import APIRouter

from security_service import build_pii_columns, compute_top_pii_tables

router = APIRouter()


@router.get("/api/security/top-tables")
def top_tables():
    return {"top_tables": compute_top_pii_tables(build_pii_columns())}
