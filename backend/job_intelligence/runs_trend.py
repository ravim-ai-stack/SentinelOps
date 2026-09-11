"""Job intelligence page — 'Job runs trend' line chart. Sample data (see stats.py)."""

from fastapi import APIRouter

router = APIRouter()

_POINTS = [
    {"label": "Sep 1", "success": 12, "failed": 3, "cancelled": 1},
    {"label": "Sep 5", "success": 13, "failed": 2, "cancelled": 1},
    {"label": "Sep 10", "success": 11, "failed": 3, "cancelled": 1},
    {"label": "Sep 15", "success": 14, "failed": 2, "cancelled": 1},
    {"label": "Sep 20", "success": 13, "failed": 4, "cancelled": 1},
    {"label": "Sep 25", "success": 16, "failed": 3, "cancelled": 2},
    {"label": "Sep 30", "success": 13, "failed": 3, "cancelled": 1},
]


@router.get("/api/jobs/runs-trend")
def runs_trend():
    return {"points": _POINTS}
