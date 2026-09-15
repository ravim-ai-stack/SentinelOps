// ---------------------------------------------------------------------
// api.js — all connections from the UI to the SentinelOps backend.
// One function per backend panel endpoint (backend/<page>/<panel>.py) so
// app.js never calls fetch() directly. Mirrors the backend 1:1: one file
// per panel, one API call per panel.
// ---------------------------------------------------------------------

const CATALOG_API_BASE = 'http://localhost:3001';

async function fetchJson(path) {
  const res = await fetch(CATALOG_API_BASE + path);
  if (!res.ok) throw new Error(`${path} failed: ${res.status} ${res.statusText}`);
  return res.json();
}

// ---- Dashboard ----
function getDashboardStats() { return fetchJson('/api/dashboard/stats'); }
function getJobHealthOverview() { return fetchJson('/api/dashboard/job-health-overview'); }
function getDashboardAccessRisks() { return fetchJson('/api/dashboard/access-risks'); }
function getDashboardPiiOverview() { return fetchJson('/api/dashboard/pii-overview'); }
function getDashboardJobsTrend() { return fetchJson('/api/dashboard/jobs-trend'); }
function getTopCatalogs() { return fetchJson('/api/dashboard/top-catalogs'); }

// ---- Catalog explorer ----
function getCatalogStats() { return fetchJson('/api/catalog/stats'); }
function getCatalogTree(search, tag) {
  const params = new URLSearchParams();
  if (search) params.set('search', search);
  if (tag) params.set('tag', tag);
  const qs = params.toString();
  return fetchJson('/api/catalog/tree' + (qs ? `?${qs}` : ''));
}
function getCatalogTags() { return fetchJson('/api/catalog/tags'); }
function getCatalogAccess(catalog) {
  return fetchJson(`/api/catalog/${encodeURIComponent(catalog)}/access`);
}

// ---- Access governance ----
function getAccessStats() { return fetchJson('/api/access/stats'); }
function getAccessUsers() { return fetchJson('/api/access/users'); }
function getMostActiveUsers() { return fetchJson('/api/access/most-active'); }
function getHighRiskUsers() { return fetchJson('/api/access/high-risk'); }
function getAccessGroups() { return fetchJson('/api/access/groups'); }
function getAccessGrants() { return fetchJson('/api/access/grants'); }
function getEffectiveAccess(user) {
  return fetchJson(`/api/access/effective/${encodeURIComponent(user)}`);
}
function getUserCatalogAccess(user) {
  return fetchJson(`/api/access/catalog-access/${encodeURIComponent(user)}`);
}
function getCatalogInspect(catalog) {
  return fetchJson(`/api/access/catalog-inspect/${encodeURIComponent(catalog)}`);
}

// ---- Job intelligence ----
function getJobsStats() { return fetchJson('/api/jobs/stats'); }
function getJobRunStatus() { return fetchJson('/api/jobs/run-status'); }
function getFailuresByCause() { return fetchJson('/api/jobs/failures-by-cause'); }
function getJobsRunsTrend() { return fetchJson('/api/jobs/runs-trend'); }
function getJobRuns() { return fetchJson('/api/jobs/runs'); }
function getJobDetails(jobId) {
  return fetchJson(`/api/jobs/${encodeURIComponent(jobId)}/details`);
}

// ---- Data security ----
function getSecurityStats() { return fetchJson('/api/security/stats'); }
function getRiskDistribution() { return fetchJson('/api/security/risk-distribution'); }
function getSecurityCategoryBreakdown() { return fetchJson('/api/security/category-breakdown'); }
function getTopPiiTables() { return fetchJson('/api/security/top-tables'); }
function getSensitiveTables() { return fetchJson('/api/security/sensitive-tables'); }
function getRecentClassifications() { return fetchJson('/api/security/recent-classifications'); }
