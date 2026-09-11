"""Job intelligence page — 'Jobs (Failed Runs Only)' table. Sample data (see stats.py)."""

from fastapi import APIRouter

router = APIRouter()

_FAILED_JOBS = [
    {
        "job_id": "revenue_etl",
        "job_name": "finance_etl",
        "tag": "Finance",
        "status": "Failed",
        "last_run": "Sep 30, 2024 10:24 AM",
        "duration": "1h 3m 34s",
        "root_cause": "Job failed due to driver out-of-memory error caused by large data shuffle during aggr…",
        "rca_status": "Completed",
    },
    {
        "job_id": "pii_tokenization",
        "job_name": "pii_tokenization",
        "tag": "Healthcare",
        "status": "Failed",
        "last_run": "Sep 30, 2024 04:32 PM",
        "duration": "8m 12s",
        "root_cause": "Job failed due to permission denied while writing to /mnt/datalake/healthcare/pii/ to…",
        "rca_status": "Completed",
    },
    {
        "job_id": "customer_data_sync",
        "job_name": "customer_data_sync",
        "tag": "Retail",
        "status": "Failed",
        "last_run": "Sep 29, 2024 01:06 PM",
        "duration": "5m 21s",
        "root_cause": "Job failed due to JDBC connection timeout while reading from source database (Timeo…",
        "rca_status": "Completed",
    },
    {
        "job_id": "marketing_reports",
        "job_name": "marketing_reports",
        "tag": "Marketing",
        "status": "Failed",
        "last_run": "Sep 29, 2024 11:10 AM",
        "duration": "9m 48s",
        "root_cause": "Job failed due to Delta write conflict. Concurrent update detected on table marketin…",
        "rca_status": "Completed",
    },
    {
        "job_id": "inventory_pipeline",
        "job_name": "inventory_pipeline",
        "tag": "Supply Chain",
        "status": "Failed",
        "last_run": "Sep 29, 2024 08:45 AM",
        "duration": "12m 11s",
        "root_cause": "Job failed due to schema mismatch in input data. Column 'item_id' not found in sourc…",
        "rca_status": "Completed",
    },
    {
        "job_id": "billing_reconciliation",
        "job_name": "billing_reconciliation",
        "tag": "Finance",
        "status": "Failed",
        "last_run": "Sep 28, 2024 10:18 PM",
        "duration": "15m 33s",
        "root_cause": "Job failed due to arithmetic overflow error while computing aggregates on large data…",
        "rca_status": "Completed",
    },
]


@router.get("/api/jobs/failed")
def failed_jobs():
    return {"jobs": _FAILED_JOBS}
