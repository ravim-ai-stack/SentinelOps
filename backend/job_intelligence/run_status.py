"""Job intelligence page — 'Job run status' donut. Sample data (see stats.py)."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/jobs/run-status")
def run_status():
    return {"total_runs": 120, "success": 92, "failed": 20, "cancelled": 8}
