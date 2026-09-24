-- ===================================================================
-- SentinelOps: Catalog Usage Lineage Setup Script (Full Setup)
-- ===================================================================
-- Run this script in ANY Databricks account to set up everything from
-- scratch: catalog, schema, 6 pre-aggregated tables, data load, and
-- service principal grants for the SentinelOps app.
--
-- Prerequisites:
--   - You must be an account admin (to read system.access + system.lakeflow)
--   - You must have CREATE CATALOG permission
--
-- REPLACE THESE TWO VALUES before running:
--   <CATALOG>                   -> your catalog name (e.g. sentinelops_catalog)
--   <APP_SERVICE_PRINCIPAL_ID>  -> your app's service principal client ID
--
-- How to find your app's service principal ID:
--   databricks apps list --output JSON
--   Look for "service_principal_client_id" in the output.
--   It looks like a UUID, e.g. cbdd7196-ff26-4d0f-bc86-298a8b435d7d
-- ===================================================================

-- ===================================================================
-- STEP 1: Create the catalog (skip if already exists)
-- ===================================================================

CREATE CATALOG IF NOT EXISTS <CATALOG>
COMMENT 'Catalog for SentinelOps app supporting tables';

-- ===================================================================
-- STEP 2: Create the schema
-- ===================================================================

CREATE SCHEMA IF NOT EXISTS <CATALOG>.sentinelops
COMMENT 'SentinelOps app supporting tables and materialized views';

-- ===================================================================
-- STEP 3: Create all 6 pre-aggregated tables
-- ===================================================================
-- These tables mirror system.access.table_lineage, system.lakeflow.*, and
-- SCIM user/group data so the service principal can read them without
-- metastore-admin grants on the system catalog or workspace admin access
-- for the SCIM REST API.
-- ===================================================================

-- 3a. Catalog usage lineage (for dashboard 'Most Used Catalog' chart)
CREATE TABLE IF NOT EXISTS <CATALOG>.sentinelops.catalog_usage_daily (
    catalog_name STRING,
    event_date   DATE,
    access_count BIGINT
)
COMMENT 'Pre-aggregated catalog access lineage for SentinelOps dashboard';

-- 3b. Job runs (for Jobs page - runs list, running count, run detail)
CREATE TABLE IF NOT EXISTS <CATALOG>.sentinelops.job_runs (
    workspace_id      STRING,
    job_id            STRING,
    run_id            STRING,
    period_start_time TIMESTAMP,
    period_end_time   TIMESTAMP,
    result_state      STRING,
    termination_code  STRING,
    run_name          STRING
)
COMMENT 'Copy of system.lakeflow.job_run_timeline for SentinelOps app';

-- 3c. Job registry (for Jobs page - job names and tags)
CREATE TABLE IF NOT EXISTS <CATALOG>.sentinelops.job_registry (
    workspace_id STRING,
    job_id       STRING,
    name         STRING,
    tags         MAP<STRING, STRING>
)
COMMENT 'Latest job names/tags from system.lakeflow.jobs for SentinelOps';

-- 3d. Job task runs (for Jobs page - task-level RCA detail)
CREATE TABLE IF NOT EXISTS <CATALOG>.sentinelops.job_task_runs (
    workspace_id      STRING,
    job_id            STRING,
    job_run_id        STRING,
    run_id            STRING,
    task_key          STRING,
    period_start_time TIMESTAMP,
    period_end_time   TIMESTAMP,
    result_state      STRING,
    termination_code  STRING
)
COMMENT 'Copy of system.lakeflow.job_task_run_timeline for SentinelOps app';

-- 3e. SCIM users (for Access governance - user list)
CREATE TABLE IF NOT EXISTS <CATALOG>.sentinelops.scim_users (
    user_id      STRING,
    user_name    STRING,
    display_name STRING,
    active       BOOLEAN
)
COMMENT 'SCIM users snapshot for SentinelOps access governance';

-- 3f. SCIM groups (for Access governance - group membership)
CREATE TABLE IF NOT EXISTS <CATALOG>.sentinelops.scim_groups (
    group_id     STRING,
    display_name STRING,
    members_json STRING  -- JSON array of [{"value": "user_id", "display": "email"}]
)
COMMENT 'SCIM groups snapshot for SentinelOps access governance';

-- ===================================================================
-- STEP 4: Populate all tables (initial load)
-- ===================================================================

-- 4a. Catalog usage daily
INSERT OVERWRITE <CATALOG>.sentinelops.catalog_usage_daily
SELECT
    catalog_name,
    event_date,
    COUNT(*) AS access_count
FROM (
    SELECT source_table_catalog AS catalog_name, event_date
    FROM system.access.table_lineage
    WHERE source_table_catalog IS NOT NULL
      AND source_table_catalog != 'system'
    UNION ALL
    SELECT target_table_catalog AS catalog_name, event_date
    FROM system.access.table_lineage
    WHERE target_table_catalog IS NOT NULL
      AND target_table_catalog != 'system'
)
GROUP BY catalog_name, event_date;

-- 4b. Job runs (last 90 days)
INSERT OVERWRITE <CATALOG>.sentinelops.job_runs
SELECT
    workspace_id,
    job_id,
    run_id,
    period_start_time,
    period_end_time,
    result_state,
    termination_code,
    run_name
FROM system.lakeflow.job_run_timeline
WHERE period_start_time >= dateadd(DAY, -90, current_timestamp());

-- 4c. Job registry (latest row per job)
INSERT OVERWRITE <CATALOG>.sentinelops.job_registry
SELECT job_id, name, tags, workspace_id
FROM (
    SELECT
        workspace_id,
        job_id,
        name,
        tags,
        ROW_NUMBER() OVER (
            PARTITION BY workspace_id, job_id
            ORDER BY change_time DESC
        ) AS rn
    FROM system.lakeflow.jobs
    WHERE delete_time IS NULL
)
WHERE rn = 1;

-- 4d. Job task runs (last 90 days)
INSERT OVERWRITE <CATALOG>.sentinelops.job_task_runs
SELECT
    workspace_id,
    job_id,
    job_run_id,
    run_id,
    task_key,
    period_start_time,
    period_end_time,
    result_state,
    termination_code
FROM system.lakeflow.job_task_run_timeline
WHERE period_start_time >= dateadd(DAY, -90, current_timestamp());

-- 4e. SCIM users and groups (populated via Python, not SQL)
-- See STEP 9 for the Python refresh script that populates these tables.
-- The SCIM REST API requires workspace admin access, so these tables
-- must be refreshed by an admin (or a job running as an admin SP).

-- ===================================================================
-- STEP 5: Grant the app service principal access
-- ===================================================================
-- Replace <APP_SERVICE_PRINCIPAL_ID> with your app's SP client ID.
-- You can find it by running: databricks apps list --output JSON
-- ===================================================================

GRANT USE CATALOG ON CATALOG <CATALOG> TO `<APP_SERVICE_PRINCIPAL_ID>`;

GRANT USE SCHEMA ON SCHEMA <CATALOG>.sentinelops TO `<APP_SERVICE_PRINCIPAL_ID>`;

GRANT SELECT ON TABLE <CATALOG>.sentinelops.catalog_usage_daily TO `<APP_SERVICE_PRINCIPAL_ID>`;

GRANT SELECT ON TABLE <CATALOG>.sentinelops.job_runs TO `<APP_SERVICE_PRINCIPAL_ID>`;

GRANT SELECT ON TABLE <CATALOG>.sentinelops.job_registry TO `<APP_SERVICE_PRINCIPAL_ID>`;

GRANT SELECT ON TABLE <CATALOG>.sentinelops.job_task_runs TO `<APP_SERVICE_PRINCIPAL_ID>`;

GRANT SELECT ON TABLE <CATALOG>.sentinelops.scim_users TO `<APP_SERVICE_PRINCIPAL_ID>`;

GRANT SELECT ON TABLE <CATALOG>.sentinelops.scim_groups TO `<APP_SERVICE_PRINCIPAL_ID>`;

-- ===================================================================
-- STEP 6: Verify the grants
-- ===================================================================

SELECT grantee, privilege_type, table_catalog, table_schema, table_name
FROM system.information_schema.table_privileges
WHERE table_catalog = '<CATALOG>'
  AND table_schema = 'sentinelops'
  AND grantee = '<APP_SERVICE_PRINCIPAL_ID>'
ORDER BY table_name;

-- Expected: 6 rows (one SELECT per table)

SELECT grantee, privilege_type
FROM system.information_schema.schema_privileges
WHERE catalog_name = '<CATALOG>'
  AND schema_name = 'sentinelops'
  AND grantee = '<APP_SERVICE_PRINCIPAL_ID>';

-- ===================================================================
-- STEP 7: Verify all tables have data
-- ===================================================================

SELECT 'catalog_usage_daily' AS table_name, COUNT(*) AS row_count FROM <CATALOG>.sentinelops.catalog_usage_daily
UNION ALL
SELECT 'job_runs', COUNT(*) FROM <CATALOG>.sentinelops.job_runs
UNION ALL
SELECT 'job_registry', COUNT(*) FROM <CATALOG>.sentinelops.job_registry
UNION ALL
SELECT 'job_task_runs', COUNT(*) FROM <CATALOG>.sentinelops.job_task_runs
UNION ALL
SELECT 'scim_users', COUNT(*) FROM <CATALOG>.sentinelops.scim_users
UNION ALL
SELECT 'scim_groups', COUNT(*) FROM <CATALOG>.sentinelops.scim_groups;

-- ===================================================================
-- STEP 8: Test the query the app will run
-- ===================================================================

SELECT catalog_name AS cat, SUM(access_count) AS count
FROM <CATALOG>.sentinelops.catalog_usage_daily
WHERE event_date >= current_date() - INTERVAL 30 DAYS
GROUP BY catalog_name
ORDER BY count DESC
LIMIT 5;

-- ===================================================================
-- STEP 9: Create the refresh SQL file
-- ===================================================================
-- Save the following as refresh_sentinelops.sql in your workspace.
-- Then create a Databricks Job that runs this file every 1 hour.
--
-- -- 1. Catalog usage daily
-- INSERT OVERWRITE <CATALOG>.sentinelops.catalog_usage_daily
-- SELECT catalog_name, event_date, COUNT(*) AS access_count
-- FROM (
--     SELECT source_table_catalog AS catalog_name, event_date
--     FROM system.access.table_lineage
--     WHERE source_table_catalog IS NOT NULL AND source_table_catalog != 'system'
--     UNION ALL
--     SELECT target_table_catalog AS catalog_name, event_date
--     FROM system.access.table_lineage
--     WHERE target_table_catalog IS NOT NULL AND target_table_catalog != 'system'
-- ) GROUP BY catalog_name, event_date;
--
-- -- 2. Job runs (last 90 days)
-- INSERT OVERWRITE <CATALOG>.sentinelops.job_runs
-- SELECT workspace_id, job_id, run_id, period_start_time, period_end_time,
--        result_state, termination_code, run_name
-- FROM system.lakeflow.job_run_timeline
-- WHERE period_start_time >= dateadd(DAY, -90, current_timestamp());
--
-- -- 3. Job registry (latest per job)
-- INSERT OVERWRITE <CATALOG>.sentinelops.job_registry
-- SELECT job_id, name, tags, workspace_id FROM (
--     SELECT workspace_id, job_id, name, tags,
--            ROW_NUMBER() OVER (PARTITION BY workspace_id, job_id ORDER BY change_time DESC) AS rn
--     FROM system.lakeflow.jobs WHERE delete_time IS NULL
-- ) WHERE rn = 1;
--
-- -- 4. Job task runs (last 90 days)
-- INSERT OVERWRITE <CATALOG>.sentinelops.job_task_runs
-- SELECT workspace_id, job_id, job_run_id, run_id, task_key,
--        period_start_time, period_end_time, result_state, termination_code
-- FROM system.lakeflow.job_task_run_timeline
-- WHERE period_start_time >= dateadd(DAY, -90, current_timestamp());
--
-- -- 5. SCIM users and groups (Python task, NOT SQL)
-- Create a SEPARATE job task (Python) that runs this script to refresh
-- SCIM data. The SCIM REST API requires workspace admin access.
--
-- import json
-- from databricks.sdk import WorkspaceClient
-- from pyspark.sql import Row
--
-- w = WorkspaceClient()
--
-- users = [Row(user_id=u.id, user_name=u.user_name,
--              display_name=u.display_name or "",
--              active=bool(u.active if u.active is not None else True))
--          for u in w.users.list()]
--
-- groups = []
-- for g in w.groups.list():
--     members = [{"value": m.value, "display": m.display or ""} for m in (g.members or [])]
--     groups.append(Row(group_id=g.id, display_name=g.display_name,
--                      members_json=json.dumps(members)))
--
-- spark.createDataFrame(users).write.mode("overwrite").saveAsTable("<CATALOG>.sentinelops.scim_users")
-- spark.createDataFrame(groups).write.mode("overwrite").saveAsTable("<CATALOG>.sentinelops.scim_groups")
--
-- Job config (two tasks):
--   Task 1 (SQL): refreshes catalog_usage_daily, job_runs, job_registry, job_task_runs
--     - SQL file: /Workspace/Users/<email>/refresh_sentinelops.sql
--     - SQL warehouse: <your-warehouse-id>
--   Task 2 (Python): refreshes scim_users, scim_groups
--     - Notebook or Python file with the script above
--     - Depends on: Task 1
--   Schedule: Periodic, every 1 hour
-- ===================================================================

-- ===================================================================
-- STEP 10: Update the app code
-- ===================================================================
-- THREE files need changes:
--
-- 10a. dashboard_service.py — change _LINEAGE_SQL:
--
--   OLD:
--     SELECT cat, sum(n) AS count FROM (
--       SELECT source_table_catalog AS cat, count(*) AS n
--       FROM system.access.table_lineage ... GROUP BY source_table_catalog
--       UNION ALL
--       SELECT target_table_catalog AS cat, count(*) AS n
--       FROM system.access.table_lineage ... GROUP BY target_table_catalog
--     ) GROUP BY cat ORDER BY count DESC LIMIT {limit}
--
--   NEW:
--     SELECT catalog_name AS cat, sum(access_count) AS count
--     FROM <CATALOG>.sentinelops.catalog_usage_daily
--     WHERE event_date >= current_date() - INTERVAL {days} DAYS
--     GROUP BY catalog_name ORDER BY count DESC LIMIT {limit}
--
-- 10b. job_service.py — change _query() + all SQL table references:
--
--   In _query(), remove the viewer-token branch, always use run_query():
--     def _query(request, statement, params):
--         return run_query(statement, params)
--
--   Replace all system.lakeflow table references:
--     system.lakeflow.job_run_timeline      -> <CATALOG>.sentinelops.job_runs
--     system.lakeflow.jobs                  -> <CATALOG>.sentinelops.job_registry
--     system.lakeflow.job_task_run_timeline -> <CATALOG>.sentinelops.job_task_runs
--
--   Simplify _REGISTRY_SQL (pre-aggregation already done in table):
--     SELECT job_id, name, tags
--     FROM <CATALOG>.sentinelops.job_registry
--     WHERE 1 = 1 {_workspace_clause()}
--
-- 10c. grants_service.py — change SCIM fetchers to use pre-agg tables:
--
--   Remove: from databricks_client import run_query, scim_get_all
--   Add:    import json  (at top of file)
--           from databricks_client import run_query
--
--   Replace fetch_scim_users():
--     OLD: return scim_get_all("/api/2.0/preview/scim/v2/Users")
--     NEW: return run_query("""
--          SELECT user_id AS id, user_name AS "userName",
--                 display_name AS "displayName", active
--          FROM <CATALOG>.sentinelops.scim_users """)
--
--   Replace fetch_scim_groups():
--     OLD: return scim_get_all("/api/2.0/preview/scim/v2/Groups")
--     NEW: rows = run_query("""
--              SELECT group_id AS id, display_name AS "displayName",
--                     members_json
--              FROM <CATALOG>.sentinelops.scim_groups """)
--          for r in rows:
--              r["members"] = json.loads(r.pop("members_json") or "[]")
--          return rows
--
-- Then redeploy:
--   databricks apps deploy sentinelops --branch main
-- ===================================================================