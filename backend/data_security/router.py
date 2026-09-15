"""Combines every Data security panel router into one, mounted by main.py."""

from fastapi import APIRouter

from data_security import (
    category_breakdown,
    recent_classifications,
    risk_distribution,
    sensitive_tables,
    stats,
    top_tables,
)

router = APIRouter(tags=["data-security"])
router.include_router(stats.router)
router.include_router(risk_distribution.router)
router.include_router(category_breakdown.router)
router.include_router(top_tables.router)
router.include_router(sensitive_tables.router)
router.include_router(recent_classifications.router)
