-- ===================================================================
-- SentinelOps: Catalog Usage Lineage Setup Script (Full Setup)
-- ===================================================================
-- Run this script in ANY Databricks account to set up everything from
-- scratch: catalog, schema, 4 pre-aggregated tables, data load, and
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
-- STEP 3: Create all 4 pre-aggregated tables
-- ===================================================================
-- These tables mirror system.access.table_lineage and system.lakeflow.*
-- so the service principal can read them without metastore-admin grants
-- on the `system` catalog.
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

-- ===================================================================
-- STEP 6: Verify the grants
-- ===================================================================

SELECT grantee, privilege_type, table_catalog, table_schema, table_name
FROM system.information_schema.table_privileges
WHERE table_catalog = '<CATALOG>'
  AND table_schema = 'sentinelops'
  AND grantee = '<APP_SERVICE_PRINCIPAL_ID>'
ORDER BY table_name;

-- Expected: 4 rows (one SELECT per table)

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
SELECT 'job_task_runs', COUNT(*) FROM <CATALOG>.sentinelops.job_task_runs;

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
-- Job config:
--   - Task type: SQL
--   - SQL file: /Workspace/Users/<email>/refresh_sentinelops.sql
--   - SQL warehouse: <your-warehouse-id>
--   - Schedule: Periodic, every 1 hour
-- ===================================================================

-- ===================================================================
-- STEP 10: Update the app code
-- ===================================================================
-- TWO files need changes:
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
-- Then redeploy:
--   databricks apps deploy sentinelops --branch main
-- ===================================================================