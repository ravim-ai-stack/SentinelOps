"""Job intelligence page — 'Failures by root cause' bars. Sample data (see stats.py)."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/jobs/failures-by-cause")
def failures_by_cause():
    return {"causes": [
        {"cause": "OOM on driver", "count": 6},
        {"cause": "State write fail", "count": 5},
        {"cause": "Permission error", "count": 4},
        {"cause": "Data read timeout", "count": 3},
        {"cause": "Network issue", "count": 2},
    ]}
