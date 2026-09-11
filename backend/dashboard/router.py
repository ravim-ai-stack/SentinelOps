"""Combines every Dashboard panel router into one, mounted by main.py."""

from fastapi import APIRouter

from dashboard import access_risks, job_health_overview, jobs_trend, pii_overview, stats, top_catalogs

router = APIRouter(tags=["dashboard"])
router.include_router(stats.router)
router.include_router(job_health_overview.router)
router.include_router(access_risks.router)
router.include_router(pii_overview.router)
router.include_router(jobs_trend.router)
router.include_router(top_catalogs.router)
