"""Combines every Job intelligence panel router into one, mounted by main.py."""

from fastapi import APIRouter

from job_intelligence import failed_jobs, failures_by_cause, job_details, run_status, runs_trend, stats

router = APIRouter(tags=["job-intelligence"])
router.include_router(stats.router)
router.include_router(run_status.router)
router.include_router(failures_by_cause.router)
router.include_router(runs_trend.router)
router.include_router(failed_jobs.router)
router.include_router(job_details.router)
