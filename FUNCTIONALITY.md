# SentinelOps — Module Functionality Reference

Every panel across the 5 pages: what it does, its category tag, and what actually
happens behind the scenes when it loads. See `ARCHITECTURE.txt` for the overall
system layout.

**Category tags:** `metadata` (structural inventory), `governance` (who can access
what), `risk_scoring` (rule-based risk flags), `ai_context` (sensitivity / root-cause
narrative signals), `usage_analytics` (access-frequency ranking), `job_monitoring`
(job run health — currently mock).

22 of 28 panels are live; the 6 Job Intelligence panels are mock (no job-run data
source is queried anywhere in this backend yet — see `ARCHITECTURE.txt`).

---

## Dashboard

| Module | Tag | Backend file | Data source | Behind the scenes |
|---|---|---|---|---|
| Stats | `metadata` | `dashboard/stats.py` | Live — Unity Catalog + SCIM | `build_full_tree()` queries `information_schema.{catalogs,schemata,tables,routines,volumes}` + UC REST models endpoint, assembles one tree; `summarize()` counts catalogs/schemas/tables. User/group counts come from separate SCIM calls. |
| Job Health Overview | `job_monitoring` | `dashboard/job_health_overview.py` | Live — `system.lakeflow.job_run_timeline` | `fetch_job_runs(30)` pulls `JOB_RUN` rows from the last 30 days. Each `result_state` is bucketed: SUCCESS/SUCCESS_WITH_FAILURES → success; CANCELED/TIMEDOUT/DISABLED/EXCLUDED → cancelled; everything else → failed. Counted per bucket. |
| Access Risks | `risk_scoring` | `dashboard/access_risks.py` | Live — UC grants | Pulls all UC grants (4 queries: catalog/schema/table/volume privileges). Flags each grant broad (ALL_PRIVILEGES/MODIFY, or catalog-level beyond USE_CATALOG/USE_SCHEMA) or sensitive (object path matches a keyword like pii/finance/hr/security). Counts distinct flagged objects per level. |
| Data Security & PII (mini) | `ai_context` | `dashboard/pii_overview.py` | Live — PII scan | `build_pii_columns()` scans `information_schema.columns`; each column name is regex-matched against 7 PII category patterns (SSN, credit card, health records, email, phone, address, DOB) with a risk tier + confidence score. Tallied by category. |
| Jobs Trend | `job_monitoring` | `dashboard/jobs_trend.py` | Live — `system.lakeflow.job_run_timeline` | Same job-run query (14-day window), grouped by calendar date and result-state bucket into one {date, success, failed, cancelled} point per day, zero-filled for quiet days. |
| Most Frequently Used Catalog | `usage_analytics` | `dashboard/top_catalogs.py` | Live — `system.access.table_lineage` | Counts how often each catalog appears as the source or target of a lineage event in the last 30 days, excludes Databricks' own `system` catalog (it would otherwise dominate), returns top 5. |

## Catalog Explorer

| Module | Tag | Backend file | Data source | Behind the scenes |
|---|---|---|---|---|
| Stats | `metadata` | `catalog_explorer/stats.py` | Live — Unity Catalog | Identical `build_full_tree()` + `summarize()` as Dashboard Stats, returns all 6 counts (catalogs/schemas/tables/functions/volumes/models). |
| Tree | `metadata` | `catalog_explorer/tree.py` | Live — Unity Catalog | Same tree assembly; `filter_tree()` applies the search term by keeping a catalog/schema if its own name matches, or if any child object's name matches, case-insensitive, recursively. |
| Access | `governance` | `catalog_explorer/access.py` | Live — UC grants + SCIM | Filters the full grants list to `level=CATALOG AND object=<catalog>`, splits the grantee set into groups (member lists resolved via SCIM) vs individual users. |

## Access Governance

| Module | Tag | Backend file | Data source | Behind the scenes |
|---|---|---|---|---|
| Stats | `governance` | `access_governance/stats.py` | Live — SCIM + grants | Runs `compute_user_access()` for every user (direct grants + every grant belonging to a group they're in), counts how many end up with any `risk_level` set. |
| Users | `risk_scoring` | `access_governance/users.py` | Live — SCIM + grants | Same `compute_user_access()`. Risk logic: **high** if any grant is both broad and sensitive, or sensitive alone, or broad alone; **medium** if object count ≥ 10 with no other flag; else none. Full table, sorted by objects-with-access desc. |
| Most Active | `usage_analytics` | `access_governance/most_active.py` | Live — grants | Same computation, filtered to objects_with_access > 0, sorted desc, top 5. |
| High Risk | `risk_scoring` | `access_governance/high_risk.py` | Live — grants | Same computation, filtered to risk_level set, sorted by severity (high first) then high-risk object count. |
| Groups | `governance` | `access_governance/groups.py` | Live — SCIM + grants | For each group, looks only at grants made directly to that group (not aggregated from members), counts distinct objects and how many are flagged risky. |
| Grants | `governance` | `access_governance/grants.py` | Live — grants | No logic — flattens the 4 raw privilege tables (catalog/schema/table/volume) into one `{grantee, level, object, privilege}` list. |
| Effective Access | `governance` | `access_governance/effective_access.py` | Live — grants | For one chosen user: resolves their group memberships, scans all grants, tags each match as "Direct" or "Group: `<name>`", sorted by object. |

## Job Intelligence — 100% mock, no computation

| Module | Tag | Backend file | Data source | Behind the scenes |
|---|---|---|---|---|
| Stats | `job_monitoring` | `job_intelligence/stats.py` | Mock | Fixed dict: total/success/failed/running/RCA counts. No query. |
| Run Status | `job_monitoring` | `job_intelligence/run_status.py` | Mock | Fixed dict: total/success/failed/cancelled for the donut. No query. |
| Failures by Cause | `ai_context` | `job_intelligence/failures_by_cause.py` | Mock | Fixed list of 5 named causes with counts. No correlation to any real run data. |
| Runs Trend | `job_monitoring` | `job_intelligence/runs_trend.py` | Mock | Fixed 7-point daily series. No query. |
| Failed Jobs | `job_monitoring` | `job_intelligence/failed_jobs.py` | Mock | Fixed list of 6 fictional job records (name, tag, duration, root cause text). |
| Job Details | `ai_context` | `job_intelligence/job_details.py` | Mock | Fixed drawer payload (overview, root cause + confidence, stack trace, 3 recommended actions) — identical regardless of which job row is clicked. |

## Data Security

| Module | Tag | Backend file | Data source | Behind the scenes |
|---|---|---|---|---|
| Stats | `ai_context` | `data_security/stats.py` | Live — PII scan | Groups PII columns by table, counts distinct tables with ≥1 PII column, and how many of those have at least one "high" risk column. |
| Risk Distribution | `ai_context` | `data_security/risk_distribution.py` | Live — PII scan | Tallies PII columns into high/medium/low buckets (same `classify_column()` as the Dashboard mini-panel). |
| Category Breakdown | `ai_context` | `data_security/category_breakdown.py` | Live — PII scan | Tallies PII columns per category name. |
| Top Tables | `ai_context` | `data_security/top_tables.py` | Live — PII scan | Groups by table, counts PII columns per table, top 5. |
| Sensitive Tables | `ai_context` | `data_security/sensitive_tables.py` | Live — PII scan + grants | Groups by table, takes each table's max risk, resolves who has access (`find_table_grantees` at catalog/schema/table level) and splits into groups-with-members vs individual users. |
| Recent Classifications | `ai_context` | `data_security/recent_classifications.py` | Live — PII scan | All PII columns sorted by their table's `last_altered` (from `information_schema.tables`), most recent 8. |
