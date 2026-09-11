"""Job intelligence page — 'Failures by root cause' bars.

Live — buckets failed runs in the last 30 days
(system.lakeflow.job_run_timeline) by their Databricks termination_code
(see job_service.cause_label), top 5 by count. This is a cheap heuristic
over real failure data, distinct from the full AI-generated RCA shown in
the Job details drawer (that's generated per-run, on demand, since it
costs a model call).
"""

from collections import Counter

from fastapi import APIRouter

from job_service import bucket_result_state, cause_label, fetch_job_runs

router = APIRouter()

WINDOW_DAYS = 30


@router.get("/api/jobs/failures-by-cause")
def failures_by_cause():
    runs = fetch_job_runs(WINDOW_DAYS)
    failed = [r for r in runs if bucket_result_state(r["result_state"]) == "failed"]
    counts = Counter(cause_label(r.get("termination_code")) for r in failed)
    causes = [{"cause": cause, "count": n} for cause, n in counts.most_common(5)]
    return {"causes": causes}
