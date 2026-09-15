"""Combines every Access governance panel router into one, mounted by main.py."""

from fastapi import APIRouter

from access_governance import effective_access, grants, groups, high_risk, most_active, stats, users

router = APIRouter(tags=["access-governance"])
router.include_router(stats.router)
router.include_router(users.router)
router.include_router(most_active.router)
router.include_router(high_risk.router)
router.include_router(groups.router)
router.include_router(grants.router)
router.include_router(effective_access.router)
