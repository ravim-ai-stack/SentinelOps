# SentinelOps — Module Functionality Reference

Every panel across the 5 pages: what it does, its category tag, and what actually
happens behind the scenes when it loads. See `ARCHITECTURE.txt` for the overall
system layout.

**Category tags:** `metadata` (structural inventory), `governance` (who can access
what), `risk_scoring` (rule-based risk flags), `ai_context` (sensitivity / root-cause
narrative signals), `usage_analytics` (access-frequency ranking), `job_monitoring`
(job run health).

All 28 panels are live. The 6 Job Intelligence panels read job-run history from
`system.lakeflow.job_run_timeline` and job name/tags from the Jobs REST API
(`job_service.py`); the Job details drawer additionally generates a real AI root
cause analysis on demand, per failed run, via a Databricks Model Serving endpoint
(`rca_service.py`, `MODEL_NAME` env var) — see the Job Intelligence section below.

Each tab below lists its elements one by one. For every element: **Tag**, **Backend
file**, **Data source**, and **How the logic is retrieved** (the exact query/API
calls and the rules applied to the result).

---

## Tab 1 — Dashboard

### Stats
- **Tag:** `metadata`
- **Backend file:** `dashboard/stats.py`
- **Data source:** Live — Unity Catalog + SCIM
- **How the logic is retrieved:** `build_full_tree()` queries `information_schema.{catalogs,schemata,tables,routines,volumes}` plus the UC REST models endpoint, and assembles one tree. `summarize()` then walks that tree to count catalogs/schemas/tables. User and group counts are fetched separately via SCIM calls.

### Job Health Overview
- **Tag:** `job_monitoring`
- **Backend file:** `dashboard/job_health_overview.py`
- **Data source:** Live — `system.lakeflow.job_run_timeline`
- **How the logic is retrieved:** `fetch_job_runs(30)` pulls `JOB_RUN` rows from the last 30 days. Each row's `result_state` is bucketed — SUCCESS/SUCCESS_WITH_FAILURES → success; CANCELED/TIMEDOUT/DISABLED/EXCLUDED → cancelled; everything else → failed — and counted per bucket.

### Access Risks
- **Tag:** `risk_scoring`
- **Backend file:** `dashboard/access_risks.py`
- **Data source:** Live — UC grants
- **How the logic is retrieved:** Pulls all UC grants via 4 queries (catalog/schema/table/volume privileges). Each grant is flagged **broad** (ALL_PRIVILEGES/MODIFY, or catalog-level beyond USE_CATALOG/USE_SCHEMA) or **sensitive** (object path matches a keyword like pii/finance/hr/security). Distinct flagged objects are counted per level.

### Data Security & PII (mini)
- **Tag:** `ai_context`
- **Backend file:** `dashboard/pii_overview.py`
- **Data source:** Live — PII scan
- **How the logic is retrieved:** `build_pii_columns()` scans `information_schema.columns`; each column name is regex-matched against 7 PII category patterns (SSN, credit card, health records, email, phone, address, DOB), each with a risk tier and confidence score. Results are tallied by category.

### Jobs Trend
- **Tag:** `job_monitoring`
- **Backend file:** `dashboard/jobs_trend.py`
- **Data source:** Live — `system.lakeflow.job_run_timeline`
- **How the logic is retrieved:** Same job-run query as Job Health Overview but over a 14-day window, grouped by calendar date and result-state bucket into one `{date, success, failed, cancelled}` point per day, zero-filled for days with no runs.

### Most Frequently Used Catalog
- **Tag:** `usage_analytics`
- **Backend file:** `dashboard/top_catalogs.py`
- **Data source:** Live — `system.access.table_lineage`
- **How the logic is retrieved:** Counts how often each catalog appears as the source or target of a lineage event in the last 30 days, excludes Databricks' own `system` catalog (it would otherwise dominate the ranking), and returns the top 5.

---

## Tab 2 — Catalog Explorer

### Stats
- **Tag:** `metadata`
- **Backend file:** `catalog_explorer/stats.py`
- **Data source:** Live — Unity Catalog
- **How the logic is retrieved:** Identical `build_full_tree()` + `summarize()` pipeline as the Dashboard's Stats panel, returning all 6 counts (catalogs/schemas/tables/functions/volumes/models).

### Tree
- **Tag:** `metadata`
- **Backend file:** `catalog_explorer/tree.py`
- **Data source:** Live — Unity Catalog
- **How the logic is retrieved:** Same tree assembly as Stats. `filter_tree()` then applies the search term, keeping a catalog/schema if its own name matches, or if any child object's name matches — case-insensitive, applied recursively.

### Access
- **Tag:** `governance`
- **Backend file:** `catalog_explorer/access.py`
- **Data source:** Live — UC grants + SCIM
- **How the logic is retrieved:** Filters the full grants list to `level=CATALOG AND object=<catalog>`, then splits the grantee set into groups (member lists resolved via SCIM) vs. individual users.

---

## Tab 3 — Access Governance

### Stats
- **Tag:** `governance`
- **Backend file:** `access_governance/stats.py`
- **Data source:** Live — SCIM + grants
- **How the logic is retrieved:** Runs `compute_user_access()` for every user (direct grants + every grant belonging to a group they're in), then counts how many users end up with any `risk_level` set.

### Users
- **Tag:** `risk_scoring`
- **Backend file:** `access_governance/users.py`
- **Data source:** Live — SCIM + grants
- **How the logic is retrieved:** Same `compute_user_access()` per user. Risk rule: **high** if any grant is both broad and sensitive, or sensitive alone, or broad alone; **medium** if object count ≥ 10 with no other flag; otherwise **none**. Full table sorted by objects-with-access, descending.

### Most Active
- **Tag:** `usage_analytics`
- **Backend file:** `access_governance/most_active.py`
- **Data source:** Live — grants
- **How the logic is retrieved:** Same access computation as Users, filtered to `objects_with_access > 0`, sorted descending, top 5.

### High Risk
- **Tag:** `risk_scoring`
- **Backend file:** `access_governance/high_risk.py`
- **Data source:** Live — grants
- **How the logic is retrieved:** Same access computation, filtered to rows with `risk_level` set, sorted by severity (high first) then by high-risk object count.

### Groups
- **Tag:** `governance`
- **Backend file:** `access_governance/groups.py`
- **Data source:** Live — SCIM + grants
- **How the logic is retrieved:** For each group, looks only at grants made directly to that group (not aggregated from members), counts distinct objects, and how many of those are flagged risky.

### Grants
- **Tag:** `governance`
- **Backend file:** `access_governance/grants.py`
- **Data source:** Live — grants
- **How the logic is retrieved:** No scoring logic — flattens the 4 raw privilege tables (catalog/schema/table/volume) into one `{grantee, level, object, privilege}` list.

### Effective Access
- **Tag:** `governance`
- **Backend file:** `access_governance/effective_access.py`
- **Data source:** Live — grants
- **How the logic is retrieved:** For one chosen user, resolves their group memberships, scans all grants, tags each match as "Direct" or "Group: `<name>`", sorted by object.

---

## Tab 4 — Job Intelligence

### Stats
- **Tag:** `job_monitoring`
- **Backend file:** `job_intelligence/stats.py`
- **Data source:** Live — `system.lakeflow.job_run_timeline` + Jobs REST API
- **How the logic is retrieved:** `summarize_run_status(fetch_job_runs(30))` gives total/success/failed counts; `fetch_running_job_count()` (runs with no terminal `result_state` yet) gives the running-jobs count; the RCA-generated count is however many failed runs have had a root cause analysis generated so far this session, tracked in `rca_store`.

### Run Status
- **Tag:** `job_monitoring`
- **Backend file:** `job_intelligence/run_status.py`
- **Data source:** Live — `system.lakeflow.job_run_timeline`
- **How the logic is retrieved:** Same 30-day job-run query and success/failed/cancelled bucketing as the Dashboard's Job Health Overview donut.

### Failures by Cause
- **Tag:** `job_monitoring`
- **Backend file:** `job_intelligence/failures_by_cause.py`
- **Data source:** Live — `system.lakeflow.job_run_timeline`
- **How the logic is retrieved:** Buckets failed runs from the last 30 days by their Databricks `termination_code` (mapped to a short label), returns the top 5 by count — a cheap heuristic over real failure data, distinct from the full AI RCA.

### Runs Trend
- **Tag:** `job_monitoring`
- **Backend file:** `job_intelligence/runs_trend.py`
- **Data source:** Live — `system.lakeflow.job_run_timeline`
- **How the logic is retrieved:** Same 14-day daily trend query as the Dashboard's Jobs Trend panel.

### Jobs table
- **Tag:** `job_monitoring`
- **Backend file:** `job_intelligence/job_runs.py`
- **Data source:** Live — `system.lakeflow.job_run_timeline` + Jobs REST API
- **How the logic is retrieved:** Every run (any status) from the last 30 days, joined to job name/tags via `fetch_job_registry()` (Jobs REST API `/api/2.1/jobs/list`). The UI's Status filter defaults to "Failed" so the table shows only failures out of the box, but Success/Cancelled can be picked to widen it. Root cause/RCA status is only meaningful for failed rows (full AI summary once generated and cached shows "Completed", otherwise a termination-code heuristic label shows "Pending"); non-failed rows show "—". Each row's id is its `run_id`.

### Job Details (drawer)
- **Tag:** `ai_context`
- **Backend file:** `job_intelligence/job_details.py`, `rca_service.py`
- **Data source:** Live — Jobs REST API + Model Serving
- **How the logic is retrieved:** On first "View details" for a run, fetches the run and its failed tasks' error output (`/api/2.1/jobs/runs/get`, `/api/2.1/jobs/runs/get-output`) and asks the `MODEL_NAME` serving endpoint for a root cause, fix steps, and confidence score; falls back to the run's own state message/error text if no model is configured or the call fails. Result is cached per `run_id` (`rca_store.py`) so reopening a run doesn't regenerate it.

---

## Tab 5 — Data Security

### Stats
- **Tag:** `ai_context`
- **Backend file:** `data_security/stats.py`
- **Data source:** Live — PII scan
- **How the logic is retrieved:** Groups PII columns by table, counts distinct tables with ≥1 PII column, and how many of those have at least one "high" risk column.

### Risk Distribution
- **Tag:** `ai_context`
- **Backend file:** `data_security/risk_distribution.py`
- **Data source:** Live — PII scan
- **How the logic is retrieved:** Tallies PII columns into high/medium/low buckets using the same `classify_column()` logic as the Dashboard's PII mini-panel.

### Category Breakdown
- **Tag:** `ai_context`
- **Backend file:** `data_security/category_breakdown.py`
- **Data source:** Live — PII scan
- **How the logic is retrieved:** Tallies PII columns per category name (from the same 7-category regex scan used elsewhere).

### Top Tables
- **Tag:** `ai_context`
- **Backend file:** `data_security/top_tables.py`
- **Data source:** Live — PII scan
- **How the logic is retrieved:** Groups PII columns by table, counts PII columns per table, returns the top 5.

### Sensitive Tables
- **Tag:** `ai_context`
- **Backend file:** `data_security/sensitive_tables.py`
- **Data source:** Live — PII scan + grants
- **How the logic is retrieved:** Groups by table, takes each table's max risk tier, resolves who has access via `find_table_grantees` (checked at catalog/schema/table level), and splits the result into groups-with-members vs. individual users.

### Recent Classifications
- **Tag:** `ai_context`
- **Backend file:** `data_security/recent_classifications.py`
- **Data source:** Live — PII scan
- **How the logic is retrieved:** All PII columns sorted by their table's `last_altered` value (from `information_schema.tables`), most recent 8 shown.
