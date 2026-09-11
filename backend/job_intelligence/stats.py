"""Job intelligence page — the 5 stat cards.

Sample data — no job-run data source (e.g. system.lakeflow) is queried
anywhere in this backend yet; see ARCHITECTURE.txt for the gap.
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/jobs/stats")
def jobs_stats():
    return {
        "total_runs": 120,
        "successful_runs": 92,
        "failed_runs": 20,
        "running_jobs": 6,
        "rca_generated": 18,
    }
