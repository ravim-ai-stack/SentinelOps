"""Job intelligence page — job details drawer (Overview / Root cause /
Recommended actions). Sample data (see stats.py) — in the current UI every
row opens the same illustrative drawer, which this preserves."""

from fastapi import APIRouter

router = APIRouter()

_DETAILS = {
    "overview": {
        "job_name": "revenue_etl",
        "status": "Failed",
        "tag": "Finance",
        "failed_at": "Sep 30, 2024 10:24 AM",
        "run_id": "123456789012345",
        "duration": "1h 3m 34s",
    },
    "root_cause": {
        "summary": (
            "Executor lost due to Out‑Of‑Memory error. Driver memory (8GB) "
            "exceeded during large shuffle operation in stage 4 of aggregation task. "
            "Exit code: 137. Timestamp: Sep 30, 2024, 10:24:12 AM."
        ),
        "confidence": 96,
    },
    "stack_trace": [
        "java.lang.OutOfMemoryError: Java heap space",
        "  at org.apache.spark.util.collection.unsafe.sort.UnsafeExternalSorter.",
        "      spill(UnsafeExternalSorter.java:670)",
        "  at org.apache.spark.shuffle.sort.SortShuffleWriter.write(SortShuffle",
        "      Writer.java:300)",
        "  at org.apache.spark.scheduler.ShuffleMapTask.runTask(ShuffleMapTask.",
        "      java:104)",
    ],
    "recommended_actions": [
        {
            "title": "Increase driver memory from 8GB to 16GB.",
            "description": "This will provide more headroom for large shuffle operations and prevent out-of-memory errors.",
        },
        {
            "title": "Optimize data partitioning and reduce shuffle size.",
            "description": "Repartition the data and consider using broadcast joins where applicable to minimize shuffle data volume.",
        },
        {
            "title": "Enable auto-scaling for clusters.",
            "description": "Configure dynamic allocation to automatically adjust resources based on workload demand.",
        },
    ],
}


@router.get("/api/jobs/{job_id}/details")
def job_details(job_id: str):
    return _DETAILS
