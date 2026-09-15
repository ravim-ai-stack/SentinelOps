"""Data security page — 'PII distribution by risk level' donut."""

from fastapi import APIRouter

from security_service import build_pii_columns, compute_risk_breakdown

router = APIRouter()


@router.get("/api/security/risk-distribution")
def risk_distribution():
    return {"risk_breakdown": compute_risk_breakdown(build_pii_columns())}
