"""Dashboard page — 'Access Risks' bars (objects with broad/sensitive
access, by catalog/schema/table level)."""

from fastapi import APIRouter

from dashboard_service import compute_access_risk_by_level
from grants_service import fetch_grants

router = APIRouter()


@router.get("/api/dashboard/access-risks")
def access_risks():
    return {"risk_by_level": compute_access_risk_by_level(fetch_grants())}
