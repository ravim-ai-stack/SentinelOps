-- ===================================================================
-- SentinelOps: Catalog Usage Lineage Setup Script (Full Setup)
-- ===================================================================
-- Run this script in ANY Databricks account to set up everything from
-- scratch: catalog, schema, table, data load, and service principal grants.
--
-- Prerequisites:
--   - You must be an account admin (to read system.access.table_lineage)
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
-- STEP 3: Create the pre-aggregated table
-- ===================================================================
-- This table mirrors system.access.table_lineage but is pre-aggregated
-- by catalog name and date. The service principal reads THIS table
-- instead of the system table directly, avoiding the need for
-- metastore-admin grants on the system catalog.
-- ===================================================================

CREATE TABLE IF NOT EXISTS <CATALOG>.sentinelops.catalog_usage_daily (
    catalog_name STRING,
    event_date   DATE,
    access_count BIGINT
)
COMMENT 'Pre-aggregated catalog access lineage for SentinelOps dashboard';

-- ===================================================================
-- STEP 4: Populate the table (initial load)
-- ===================================================================

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

-- ===================================================================
-- STEP 5: Grant the app service principal access
-- ===================================================================
-- Replace <APP_SERVICE_PRINCIPAL_ID> with your app's SP client ID.
-- You can find it by running: databricks apps list --output JSON
-- ===================================================================

GRANT USE CATALOG ON CATALOG <CATALOG> TO `<APP_SERVICE_PRINCIPAL_ID>`;

GRANT USE SCHEMA ON SCHEMA <CATALOG>.sentinelops TO `<APP_SERVICE_PRINCIPAL_ID>`;

GRANT SELECT ON TABLE <CATALOG>.sentinelops.catalog_usage_daily TO `<APP_SERVICE_PRINCIPAL_ID>`;

-- ===================================================================
-- STEP 6: Verify the grants
-- ===================================================================

SELECT grantee, privilege_type, table_catalog, table_schema, table_name
FROM system.information_schema.table_privileges
WHERE table_catalog = '<CATALOG>'
  AND table_schema = 'sentinelops'
  AND table_name = 'catalog_usage_daily'
  AND grantee = '<APP_SERVICE_PRINCIPAL_ID>';

SELECT grantee, privilege_type
FROM system.information_schema.schema_privileges
WHERE catalog_name = '<CATALOG>'
  AND schema_name = 'sentinelops'
  AND grantee = '<APP_SERVICE_PRINCIPAL_ID>';

-- ===================================================================
-- STEP 7: Verify the table has data
-- ===================================================================

SELECT
    COUNT(*) AS row_count,
    COUNT(DISTINCT catalog_name) AS catalog_count,
    MIN(event_date) AS earliest_date,
    MAX(event_date) AS latest_date
FROM <CATALOG>.sentinelops.catalog_usage_daily;

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
-- Save the following as refresh_catalog_usage.sql in your workspace.
-- Then create a Databricks Job that runs this file every 1 hour.
--
-- INSERT OVERWRITE <CATALOG>.sentinelops.catalog_usage_daily
-- SELECT
--     catalog_name,
--     event_date,
--     COUNT(*) AS access_count
-- FROM (
--     SELECT source_table_catalog AS catalog_name, event_date
--     FROM system.access.table_lineage
--     WHERE source_table_catalog IS NOT NULL
--       AND source_table_catalog != 'system'
--     UNION ALL
--     SELECT target_table_catalog AS catalog_name, event_date
--     FROM system.access.table_lineage
--     WHERE target_table_catalog IS NOT NULL
--       AND target_table_catalog != 'system'
-- )
-- GROUP BY catalog_name, event_date;
--
-- Job config:
--   - Task type: SQL
--   - SQL file: /Workspace/Users/<email>/refresh_catalog_usage.sql
--   - SQL warehouse: <your-warehouse-id>
--   - Schedule: Periodic, every 1 hour
-- ===================================================================

-- ===================================================================
-- STEP 10: Update the app code (dashboard_service.py)
-- ===================================================================
-- Replace the _LINEAGE_SQL in dashboard_service.py:
--
-- OLD:
--   SELECT cat, sum(n) AS count FROM (
--     SELECT source_table_catalog AS cat, count(*) AS n
--     FROM system.access.table_lineage
--     WHERE ... AND event_date >= current_date() - INTERVAL {days} DAYS
--     GROUP BY source_table_catalog
--     UNION ALL
--     SELECT target_table_catalog AS cat, count(*) AS n
--     FROM system.access.table_lineage
--     WHERE ... AND event_date >= current_date() - INTERVAL {days} DAYS
--     GROUP BY target_table_catalog
--   ) GROUP BY cat ORDER BY count DESC LIMIT {limit}
--
-- NEW:
--   SELECT catalog_name AS cat, sum(access_count) AS count
--   FROM <CATALOG>.sentinelops.catalog_usage_daily
--   WHERE event_date >= current_date() - INTERVAL {days} DAYS
--   GROUP BY catalog_name
--   ORDER BY count DESC
--   LIMIT {limit}
--
-- Then redeploy:
--   databricks apps deploy sentinelops --branch main
-- ===================================================================