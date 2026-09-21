// ---------------------------------------------------------------------
// app.js — SentinelOps UI: page navigation, rendering and DOM state.
// Data comes from api.js (loaded before this file); this file never
// calls fetch() directly. One load function per page, which fetches
// each of that page's panels from its own endpoint (see api.js) and
// hands the result to that panel's own render function.
// ---------------------------------------------------------------------

const navItems = document.querySelectorAll('.nav-item');
const pages = document.querySelectorAll('.page');
function showPage(id){
  pages.forEach(p => p.classList.add('hidden'));
  document.getElementById('page-' + id).classList.remove('hidden');
  navItems.forEach(n => n.classList.toggle('active', n.dataset.page === id));
  window.scrollTo(0,0);
}

// Global loading overlay — shown while an already-visible page is being
// refreshed (a Refresh button), hidden again once that load settles (success
// or failure). Background auto-refresh (startLiveRefresh) calls each page's
// load function directly rather than through withLoading, so it stays silent
// and never triggers this overlay.
function showGlobalLoading() {
  document.getElementById('global-loading-backdrop').classList.remove('hidden');
  document.getElementById('global-loading-box').classList.remove('hidden');
}
function hideGlobalLoading() {
  document.getElementById('global-loading-backdrop').classList.add('hidden');
  document.getElementById('global-loading-box').classList.add('hidden');
}
async function withLoading(fn) {
  showGlobalLoading();
  try {
    await fn();
  } finally {
    hideGlobalLoading();
  }
}

// Each tab fetches only its own panels, from its own endpoints, through its
// own load function — the tabs never share a fetch or a render path.
const PAGE_LOADERS = {
  dashboard: { label: 'Loading dashboard…', load: () => loadDashboard() },
  catalog: { label: 'Loading catalog explorer…', load: () => loadCatalogExplorer() },
  access: { label: 'Loading access governance…', load: () => loadAccessGovernance() },
  jobs: { label: 'Loading job intelligence…', load: () => loadJobIntelligence() },
  security: { label: 'Loading data security…', load: () => loadDataSecurity() },
};

function showPageTransitionLoading(id) {
  document.getElementById('page-transition-loading-text').textContent = PAGE_LOADERS[id].label;
  document.getElementById('page-transition-loading').classList.remove('hidden');
}
function hidePageTransitionLoading() {
  document.getElementById('page-transition-loading').classList.add('hidden');
}

// Navigates to a tab: the tab being left stays on screen (blurred, behind
// the loading card) instead of being hidden, so the loading state shows the
// actual screen rather than a blank one. The new tab only replaces it once
// its own data has finished loading. currentNavTarget guards against a stale
// navigation (e.g. the user clicks a second tab before the first tab's load
// has settled) popping its page back up after a newer one has already shown.
let currentNavTarget = null;
function navigateTo(id) {
  currentNavTarget = id;
  navItems.forEach(n => n.classList.toggle('active', n.dataset.page === id));
  showPageTransitionLoading(id);
  Promise.resolve(PAGE_LOADERS[id].load()).finally(() => {
    if (currentNavTarget !== id) return;
    hidePageTransitionLoading();
    showPage(id);
    window.scrollTo(0, 0);
  });
}
navItems.forEach(item => item.addEventListener('click', () => navigateTo(item.dataset.page)));
document.querySelectorAll('[data-goto]').forEach(el => {
  el.addEventListener('click', e => {
    e.preventDefault();
    navigateTo(el.dataset.goto);
  });
});
navigateTo('dashboard');

// ---------------------------------------------------------------------
// Real-time refresh — polls the backend on an interval and keeps each
// page's "Live · updated Xs ago" indicator in sync.
// ---------------------------------------------------------------------
const LIVE_REFRESH_MS = 30000;
const liveState = {}; // prefix -> last successful update timestamp (ms)

function markLiveUpdated(prefix) {
  liveState[prefix] = Date.now();
  const dot = document.getElementById(`${prefix}-live-dot`);
  if (dot) dot.classList.remove('stale');
  renderLiveTimestamp(prefix);
}

function markLiveStale(prefix) {
  const dot = document.getElementById(`${prefix}-live-dot`);
  if (dot) dot.classList.add('stale');
}

function renderLiveTimestamp(prefix) {
  const el = document.getElementById(`${prefix}-live-updated`);
  if (!el) return;
  const ts = liveState[prefix];
  if (!ts) { el.textContent = '–'; return; }
  const secs = Math.round((Date.now() - ts) / 1000);
  el.textContent = secs < 5 ? 'just now' : secs < 60 ? `${secs}s ago` : `${Math.round(secs / 60)}m ago`;
}

setInterval(() => Object.keys(liveState).forEach(renderLiveTimestamp), 1000);

function startLiveRefresh(pageId, loadFn) {
  setInterval(() => {
    if (!document.getElementById('page-' + pageId).classList.contains('hidden')) loadFn();
  }, LIVE_REFRESH_MS);
}

function escapeHtml(str) {
  return String(str ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

function formatDate(value) {
  if (!value) return '–';
  const d = new Date(value);
  return isNaN(d) ? String(value) : d.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
}

function highlight(text, term) {
  const safe = escapeHtml(text);
  if (!term) return safe;
  const idx = safe.toLowerCase().indexOf(term.toLowerCase());
  if (idx === -1) return safe;
  return safe.slice(0, idx) + '<mark>' + safe.slice(idx, idx + term.length) + '</mark>' + safe.slice(idx + term.length);
}

// ---------------------------------------------------------------------
// Shared chart renderers — a donut + legend from {total_runs/success/
// failed/cancelled}, and a 3-series SVG trend line from {label, success,
// failed, cancelled} points. Used by both the Dashboard and Job
// intelligence pages, which each show their own version of these charts.
// ---------------------------------------------------------------------
function renderRunStatusDonut(donutEl, totalEl, legendEl, data) {
  const total = (data.total_runs ?? (data.success + data.failed + data.cancelled)) || 0;
  totalEl.textContent = total;
  if (!total) {
    donutEl.style.background = '#eceef2';
    legendEl.innerHTML = '<div style="color:var(--muted);font-size:12.5px;">No data.</div>';
    return;
  }
  const parts = [
    { key: 'success', label: 'Success', color: 'var(--green)' },
    { key: 'failed', label: 'Failed', color: 'var(--red)' },
    { key: 'cancelled', label: 'Cancelled', color: '#c9ced9' },
  ];
  let angle = 0;
  const stops = [];
  const legendRows = [];
  parts.forEach(({ key, label, color }) => {
    const count = data[key] || 0;
    const sweep = (count / total) * 360;
    stops.push(`${color} ${angle}deg ${angle + sweep}deg`);
    legendRows.push(`<div class="legend-row"><span class="dot" style="background:${color}"></span>${label}&nbsp; ${count} (${(count / total * 100).toFixed(1)}%)</div>`);
    angle += sweep;
  });
  donutEl.style.background = `conic-gradient(${stops.join(', ')})`;
  legendEl.innerHTML = legendRows.join('');
}

function renderTrendChart(svg, points, xAxisEl) {
  const vb = svg.viewBox.baseVal;
  const w = vb.width || 560, h = vb.height || 170;
  const maxVal = Math.max(1, ...points.flatMap(p => [p.success, p.failed, p.cancelled]));
  const stepX = points.length > 1 ? w / (points.length - 1) : 0;
  const toY = v => h - (v / maxVal) * h * 0.85;
  const series = [
    { key: 'success', color: 'var(--green)' },
    { key: 'failed', color: 'var(--red)' },
    { key: 'cancelled', color: '#c9ced9' },
  ];
  const gridLines = [0, 0.25, 0.5, 0.75, 1]
    .map(f => `<line x1="0" y1="${(h * f).toFixed(1)}" x2="${w}" y2="${(h * f).toFixed(1)}"/>`)
    .join('');
  const seriesSvg = series.map(({ key, color }) => {
    const pts = points.map((p, i) => `${(i * stepX).toFixed(1)},${toY(p[key]).toFixed(1)}`).join(' ');
    const circles = points.map((p, i) => `<circle cx="${(i * stepX).toFixed(1)}" cy="${toY(p[key]).toFixed(1)}" r="3.2"/>`).join('');
    return `<polyline fill="none" stroke="${color}" stroke-width="2.4" points="${pts}"/><g fill="${color}">${circles}</g>`;
  }).join('');
  svg.innerHTML = `<g stroke="#eceef2">${gridLines}</g>${seriesSvg}`;
  if (xAxisEl) xAxisEl.innerHTML = points.map(p => `<span>${escapeHtml(p.label)}</span>`).join('');
}

// ---------------------------------------------------------------------
// Dashboard — one fetch per panel (stats, job health, access risks,
// PII overview, jobs trend, top catalogs)
// ---------------------------------------------------------------------
const RISK_LEVEL_LABELS = { CATALOG: 'Catalog', SCHEMA: 'Schemas', TABLE: 'Table' };
const RISK_LEVEL_COLORS = { CATALOG: 'var(--pink)', SCHEMA: '#e35286', TABLE: 'var(--red)' };

function setDashboardError(message) {
  const el = document.getElementById('dash-error');
  if (message) { el.textContent = message; el.classList.remove('hidden'); }
  else el.classList.add('hidden');
}

function renderDashboardRiskBars(riskByLevel) {
  const container = document.getElementById('dash-risk-bars');
  const levels = ['CATALOG', 'SCHEMA', 'TABLE'];
  const max = Math.max(1, ...levels.map(l => riskByLevel[l] || 0));
  container.innerHTML = levels.map(level => {
    const count = riskByLevel[level] || 0;
    return `
      <div class="vbar-col">
        <div class="vbar-num">${count}</div>
        <div class="vbar" style="height:${Math.max(4, Math.round((count / max) * 100))}%;background:${RISK_LEVEL_COLORS[level]};"></div>
        <div class="vbar-label">${RISK_LEVEL_LABELS[level]}</div>
      </div>
    `;
  }).join('');
}

function renderCategoryBars(container, categoryBreakdown) {
  const entries = Object.entries(categoryBreakdown).sort((a, b) => b[1] - a[1]);
  if (!entries.length) {
    container.innerHTML = '<div style="color:var(--muted);font-size:12.5px;">No PII columns detected.</div>';
    return;
  }
  const max = entries[0][1] || 1;
  container.innerHTML = entries.map(([category, count]) => `
    <div class="hbar-row">
      <span class="hbar-label" title="${escapeHtml(category)}">${escapeHtml(category)}</span>
      <div class="hbar-track"><div class="hbar-fill" style="width:${Math.round((count / max) * 100)}%;background:#8b6cf0;"></div></div>
      <span class="hbar-value">${count}</span>
    </div>
  `).join('');
}

function renderTopCatalogsBars(container, catalogs) {
  if (!catalogs.length) {
    container.innerHTML = '<div style="color:var(--muted);font-size:12.5px;">No data.</div>';
    return;
  }
  const max = catalogs[0].count || 1;
  container.innerHTML = catalogs.map(c => `
    <div class="hbar-row">
      <span class="hbar-label" title="${escapeHtml(c.catalog)}">${escapeHtml(c.catalog)}</span>
      <div class="hbar-track"><div class="hbar-fill" style="width:${Math.round((c.count / max) * 100)}%;background:#4f7cf0;"></div></div>
      <span class="hbar-value">${c.count}</span>
    </div>
  `).join('');
}

async function loadDashboard() {
  setDashboardError(null);
  try {
    const [stats, jobHealth, accessRisks, piiOverview, jobsTrend, topCatalogs] = await Promise.all([
      getDashboardStats(), getJobHealthOverview(), getDashboardAccessRisks(),
      getDashboardPiiOverview(), getDashboardJobsTrend(), getTopCatalogs(),
    ]);

    document.getElementById('dash-welcome').textContent = `Welcome, ${stats.current_user}`;
    document.getElementById('dash-stat-catalogs').textContent = stats.catalogs;
    document.getElementById('dash-stat-schemas').textContent = stats.schemas;
    document.getElementById('dash-stat-tables').textContent = stats.tables;
    document.getElementById('dash-stat-users').textContent = stats.users;
    document.getElementById('dash-stat-groups').textContent = stats.groups;

    renderRunStatusDonut(
      document.getElementById('dash-jobhealth-donut'),
      document.getElementById('dash-jobhealth-total'),
      document.getElementById('dash-jobhealth-legend'),
      jobHealth
    );
    renderDashboardRiskBars(accessRisks.risk_by_level);
    document.getElementById('dash-pii-count').textContent = piiOverview.pii_columns;
    renderCategoryBars(document.getElementById('dash-pii-bars'), piiOverview.category_breakdown);
    renderTrendChart(document.getElementById('dash-jobstrend-svg'), jobsTrend.points, document.getElementById('dash-jobstrend-x'));
    renderTopCatalogsBars(document.getElementById('dash-top-catalogs'), topCatalogs.catalogs);

    markLiveUpdated('dash');
  } catch (err) {
    setDashboardError(
      `Could not reach the Databricks backend at ${CATALOG_API_BASE} (${err.message}). ` +
      `Make sure "python main.py" is running in backend/.`
    );
    markLiveStale('dash');
  }
}

startLiveRefresh('dashboard', loadDashboard);

function renderTreeTags(tags) {
  const text = (tags && tags.length) ? tags.join(', ') : '–';
  return `<span class="tree-tags" title="${escapeHtml(text)}">${escapeHtml(text)}</span>`;
}

function renderObjectsTable(objects, term) {
  if (!objects.length) return '<div style="color:var(--muted);font-size:12.5px;padding:8px 0;">No objects in this schema.</div>';
  const rows = objects.map(o => `
    <tr>
      <td class="link-cell">${highlight(o.name, term)}</td>
      <td><span class="kind-badge ${escapeHtml(o.kind)}">${escapeHtml(o.kind)}</span></td>
      <td>${escapeHtml(o.type)}</td>
      <td>${escapeHtml(o.owner)}</td>
      <td>${(o.tags && o.tags.length) ? escapeHtml(o.tags.join(', ')) : '–'}</td>
      <td>${formatDate(o.last_altered)}</td>
    </tr>
  `).join('');
  return `
    <table>
      <tr><th>Name</th><th>Kind</th><th>Type</th><th>Owner</th><th>Tags</th><th>Last updated</th></tr>
      ${rows}
    </table>
  `;
}

// ---------------------------------------------------------------------
// Catalog explorer — 'stats' + 'tree'/'access' panels
// ---------------------------------------------------------------------

// Persisted so tree expand state and open "View access" panels survive
// re-renders triggered by search filtering and the 30s live auto-refresh.
const treeOpenCatalogs = new Set();
const treeOpenSchemas = new Set();
const catAccessOpen = new Set();       // catalog names with the access panel open
const catAccessCache = {};             // catalog name -> fetched {groups, users}
const catAccessLoading = new Set();    // catalog names currently being fetched
let lastCatalogs = [];
let lastCatalogTerm = '';

function renderCatalogAccessPanel(catalog) {
  if (catAccessLoading.has(catalog)) {
    return '<div class="access-detail-empty">Loading access…</div>';
  }
  const data = catAccessCache[catalog];
  if (!data) return '<div class="access-detail-empty">–</div>';
  if (data.error) {
    return `<div class="access-detail-empty">Could not load access: ${escapeHtml(data.error)}</div>`;
  }
  return renderAccessDetail(data, `catalog:${catalog}`);
}

function loadCatalogAccess(catalog) {
  catAccessLoading.add(catalog);
  getCatalogAccess(catalog)
    .then(data => { catAccessCache[catalog] = data; })
    .catch(err => { catAccessCache[catalog] = { groups: [], users: [], error: err.message }; })
    .finally(() => {
      catAccessLoading.delete(catalog);
      renderCatalogTree(lastCatalogs, lastCatalogTerm);
    });
}

function renderCatalogTree(catalogs, term) {
  lastCatalogs = catalogs;
  lastCatalogTerm = term;
  const container = document.getElementById('cat-tree');
  if (!catalogs.length) {
    container.innerHTML = '<div class="tree-empty">No catalogs match your search.</div>';
    return;
  }
  container.innerHTML = catalogs.map((cat, ci) => {
    const schemaCount = cat.schemas.length;
    const objectCount = cat.schemas.reduce((n, s) => n + s.objects.length, 0);
    const schemasHtml = cat.schemas.map((s, si) => {
      const schemaOpen = treeOpenSchemas.has(`${cat.catalog}.${s.schema}`);
      return `
      <div class="tree-schema${schemaOpen ? ' open' : ''}" data-cat="${ci}" data-schema="${si}">
        <div class="tree-schema-head">
          <svg class="tree-chevron" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M9 6l6 6-6 6"/></svg>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6Z"/></svg>
          <span class="tree-schema-name">${highlight(s.schema, term)}</span>
          <span class="tree-meta">${s.objects.length} object${s.objects.length === 1 ? '' : 's'}</span>
          ${renderTreeTags(s.tags)}
        </div>
        <div class="tree-objects">${renderObjectsTable(s.objects, term)}</div>
      </div>
    `;
    }).join('');
    const catOpen = treeOpenCatalogs.has(cat.catalog);
    const accessOpen = catAccessOpen.has(cat.catalog);
    return `
      <div class="tree-catalog${catOpen ? ' open' : ''}" data-cat="${ci}">
        <div class="tree-catalog-head">
          <span class="tree-row-icons">
            <svg class="tree-chevron" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M9 6l6 6-6 6"/></svg>
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 6a8 3 0 0 0 16 0A8 3 0 0 0 4 6Z"/><path d="M4 6v6a8 3 0 0 0 16 0V6"/><path d="M4 12v6a8 3 0 0 0 16 0v-6"/></svg>
          </span>
          <span class="tree-catalog-name">${highlight(cat.catalog, term)}</span>
          <span class="tree-meta">${schemaCount} schema${schemaCount === 1 ? '' : 's'} · ${objectCount} object${objectCount === 1 ? '' : 's'}</span>
          ${renderTreeTags(cat.tags)}
          <span class="tree-owner">${escapeHtml(cat.owner || '')}</span>
          <span class="tree-row-actions">
            <button class="cat-access-btn${accessOpen ? ' open' : ''}" data-catalog="${escapeHtml(cat.catalog)}">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>
              View access
            </button>
            <button class="cat-inspect-btn" data-catalog="${escapeHtml(cat.catalog)}">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.35-4.35"/></svg>
              Inspect
            </button>
          </span>
        </div>
        <div class="cat-access-panel${accessOpen ? '' : ' hidden'}" data-catalog-panel="${escapeHtml(cat.catalog)}">${accessOpen ? renderCatalogAccessPanel(cat.catalog) : ''}</div>
        <div class="tree-schemas">${schemasHtml}</div>
      </div>
    `;
  }).join('');

  container.querySelectorAll('.tree-catalog-head').forEach(head => {
    head.addEventListener('click', () => {
      const catalog = head.parentElement;
      catalog.classList.toggle('open');
      const name = catalog.querySelector('.tree-catalog-name').textContent;
      if (catalog.classList.contains('open')) treeOpenCatalogs.add(name);
      else treeOpenCatalogs.delete(name);
    });
  });
  container.querySelectorAll('.tree-schema-head').forEach(head => {
    head.addEventListener('click', () => {
      const schemaEl = head.parentElement;
      schemaEl.classList.toggle('open');
      const ci = schemaEl.dataset.cat, si = schemaEl.dataset.schema;
      const key = `${catalogs[ci].catalog}.${catalogs[ci].schemas[si].schema}`;
      if (schemaEl.classList.contains('open')) treeOpenSchemas.add(key);
      else treeOpenSchemas.delete(key);
    });
  });
  container.querySelectorAll('.cat-access-btn').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      const catalog = btn.dataset.catalog;
      if (catAccessOpen.has(catalog)) {
        catAccessOpen.delete(catalog);
      } else {
        catAccessOpen.add(catalog);
        if (!catAccessCache[catalog] && !catAccessLoading.has(catalog)) loadCatalogAccess(catalog);
      }
      renderCatalogTree(lastCatalogs, lastCatalogTerm);
    });
  });
  container.querySelectorAll('.cat-inspect-btn').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      inspectCatalog(btn.dataset.catalog);
    });
  });
  container.querySelectorAll('.cat-access-panel .access-detail-group-head').forEach(head => {
    head.addEventListener('click', () => {
      const groupEl = head.parentElement;
      groupEl.classList.toggle('open');
      if (groupEl.classList.contains('open')) secExpandedGroups.add(groupEl.dataset.gkey);
      else secExpandedGroups.delete(groupEl.dataset.gkey);
    });
  });

  // When searching, auto-expand every branch that survived the filter.
  if (term) {
    container.querySelectorAll('.tree-catalog').forEach(el => el.classList.add('open'));
    container.querySelectorAll('.tree-schema').forEach(el => el.classList.add('open'));
  }
}

function setCatalogError(message) {
  const el = document.getElementById('cat-error');
  if (message) { el.textContent = message; el.classList.remove('hidden'); }
  else el.classList.add('hidden');
}

function getCatTagFilter() {
  return document.getElementById('cat-filter-tag').value;
}

function updateCatFilterButtonState() {
  document.getElementById('cat-filter-btn').classList.toggle('solid', !!getCatTagFilter());
}

async function populateCatTagOptions() {
  const sel = document.getElementById('cat-filter-tag');
  try {
    const { tags } = await getCatalogTags();
    const current = sel.value;
    sel.innerHTML = '<option value="">All tags</option>' +
      tags.map(t => `<option value="${escapeHtml(t)}">${escapeHtml(t)}</option>`).join('');
    sel.value = tags.includes(current) ? current : '';
  } catch (err) {
    // Filter dropdown is a nice-to-have; leave it at "All tags" if this fails.
  }
}

async function loadCatalogExplorer(search) {
  setCatalogError(null);
  const tag = getCatTagFilter();
  try {
    const [stats, treeData] = await Promise.all([getCatalogStats(), getCatalogTree(search, tag), populateCatTagOptions()]);
    document.getElementById('cat-stat-catalogs').textContent = stats.catalogs;
    document.getElementById('cat-stat-schemas').textContent = stats.schemas;
    document.getElementById('cat-stat-tables').textContent = stats.tables;
    document.getElementById('cat-stat-functions').textContent = stats.functions;
    document.getElementById('cat-stat-volumes').textContent = stats.volumes;
    document.getElementById('cat-stat-models').textContent = stats.models;
    renderCatalogTree(treeData.catalogs, search);
    markLiveUpdated('cat');
  } catch (err) {
    setCatalogError(
      `Could not reach the Databricks backend at ${CATALOG_API_BASE} (${err.message}). ` +
      `Make sure "uvicorn main:app --reload --port 3001" is running in backend/.`
    );
    document.getElementById('cat-tree').innerHTML = '<div class="tree-empty">–</div>';
    markLiveStale('cat');
  }
}

let catalogSearchTimer;
document.getElementById('cat-search').addEventListener('input', e => {
  clearTimeout(catalogSearchTimer);
  catalogSearchTimer = setTimeout(() => loadCatalogExplorer(e.target.value.trim()), 300);
});
document.getElementById('cat-refresh').addEventListener('click', e => {
  e.preventDefault();
  withLoading(() => loadCatalogExplorer(document.getElementById('cat-search').value.trim()));
});

const catFilterBtn = document.getElementById('cat-filter-btn');
const catFilterPanel = document.getElementById('cat-filter-panel');
catFilterBtn.addEventListener('click', e => {
  e.stopPropagation();
  catFilterPanel.classList.toggle('hidden');
});
document.addEventListener('click', e => {
  if (!catFilterPanel.classList.contains('hidden') && !catFilterPanel.contains(e.target) && !catFilterBtn.contains(e.target)) {
    catFilterPanel.classList.add('hidden');
  }
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') catFilterPanel.classList.add('hidden');
});
document.getElementById('cat-filter-tag').addEventListener('change', () => {
  updateCatFilterButtonState();
  loadCatalogExplorer(document.getElementById('cat-search').value.trim());
});
document.getElementById('cat-filter-clear').addEventListener('click', () => {
  document.getElementById('cat-filter-tag').value = '';
  updateCatFilterButtonState();
  loadCatalogExplorer(document.getElementById('cat-search').value.trim());
  catFilterPanel.classList.add('hidden');
});

startLiveRefresh('catalog', () => loadCatalogExplorer(document.getElementById('cat-search').value.trim()));

// ---------------------------------------------------------------------
// Access governance — one fetch per panel (stats, users, most-active,
// high-risk, groups, grants, effective access)
// ---------------------------------------------------------------------
let accessUsersData = [];
let accessGroupsData = [];
let accessGrantsData = [];

function setAccessError(message) {
  const el = document.getElementById('access-error');
  if (message) { el.textContent = message; el.classList.remove('hidden'); }
  else el.classList.add('hidden');
}

function riskBadge(level) {
  if (!level) return '<span class="badge low">None</span>';
  return `<span class="badge ${level}">${level === 'high' ? 'High' : 'Medium'}</span>`;
}

function renderAccessUsersTable(users, term) {
  const tbody = document.getElementById('access-users-tbody');
  document.getElementById('access-users-count').textContent = users.length;
  if (!users.length) {
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--muted);">No users match your search.</td></tr>';
  } else {
    tbody.innerHTML = users.map(u => `
      <tr>
        <td>${highlight(u.display_name || u.user, term)}${u.display_name && u.display_name !== u.user ? `<div style="color:var(--muted);font-size:11.5px;">${escapeHtml(u.user)}</div>` : ''}</td>
        <td>${escapeHtml(u.groups.join(', ') || '–')}</td>
        <td>${escapeHtml(u.principal_type)}</td>
        <td>${u.objects_with_access}</td>
        <td class="${u.high_risk_objects ? 'risk-num' : ''}">${u.high_risk_objects}</td>
        <td>${riskBadge(u.risk_level)}</td>
      </tr>
    `).join('');
  }
  document.getElementById('access-users-foot').textContent = `Showing ${users.length} user${users.length === 1 ? '' : 's'}`;
}

function renderMostActive(mostActive) {
  const container = document.getElementById('access-most-active');
  if (!mostActive.length) {
    container.innerHTML = '<div style="color:var(--muted);font-size:12.5px;">No access data available.</div>';
    return;
  }
  const max = mostActive[0].objects_with_access || 1;
  container.innerHTML = mostActive.map(u => `
    <div class="hbar-row">
      <span class="hbar-label" title="${escapeHtml(u.user)}">${escapeHtml(u.user)}</span>
      <div class="hbar-track"><div class="hbar-fill" style="width:${Math.round((u.objects_with_access / max) * 100)}%;background:#4f7cf0;"></div></div>
      <span class="hbar-value">${u.objects_with_access}</span>
    </div>
  `).join('');
}

function renderHighRisk(highRisk) {
  const tbody = document.getElementById('access-highrisk-tbody');
  if (!highRisk.length) {
    tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--muted);">No high risk users detected.</td></tr>';
    return;
  }
  tbody.innerHTML = highRisk.map(u => `
    <tr>
      <td>${escapeHtml(u.user)}</td>
      <td>${riskBadge(u.risk_level)}</td>
      <td>${escapeHtml(u.risk_reason)}</td>
      <td>${u.high_risk_objects}</td>
    </tr>
  `).join('');
}

function applyAccessSearch(term) {
  const needle = (term || '').trim().toLowerCase();
  const users = !needle
    ? accessUsersData
    : accessUsersData.filter(u =>
        u.user.toLowerCase().includes(needle) ||
        (u.display_name || '').toLowerCase().includes(needle) ||
        u.groups.some(g => g.toLowerCase().includes(needle))
      );
  renderAccessUsersTable(users, needle);
}

// ---- Groups tab ----
function renderGroups(groups, term) {
  const tbody = document.getElementById('access-groups-tbody');
  document.getElementById('access-groups-count').textContent = groups.length;
  const needle = (term || '').trim().toLowerCase();
  const filtered = !needle ? groups : groups.filter(g => g.group.toLowerCase().includes(needle));
  if (!filtered.length) {
    tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--muted);">No groups match your search.</td></tr>';
  } else {
    tbody.innerHTML = filtered.map(g => `
      <tr>
        <td>${highlight(g.group, needle)}</td>
        <td>${g.member_count}</td>
        <td>${g.objects_with_access}</td>
        <td class="${g.high_risk_objects ? 'risk-num' : ''}">${g.high_risk_objects}</td>
      </tr>
    `).join('');
  }
  document.getElementById('access-groups-foot').textContent = `Showing ${filtered.length} of ${groups.length} groups`;
}

let groupsSearchTimer;
document.getElementById('access-groups-search').addEventListener('input', e => {
  clearTimeout(groupsSearchTimer);
  groupsSearchTimer = setTimeout(() => renderGroups(accessGroupsData, e.target.value), 200);
});

// ---- Grants tab ----
let grantsFiltered = [];
let grantsPage = 1;
const GRANTS_PAGE_SIZE = 25;

function renderGrantsPager(totalPages) {
  const pager = document.getElementById('access-grants-pager');
  if (totalPages <= 1) { pager.innerHTML = ''; return; }
  pager.innerHTML = `<button id="grants-prev">‹</button><span class="cur" style="padding:0 10px;border:none;">${grantsPage} / ${totalPages}</span><button id="grants-next">›</button>`;
  document.getElementById('grants-prev').addEventListener('click', () => {
    if (grantsPage > 1) { grantsPage--; renderGrantsPage(); }
  });
  document.getElementById('grants-next').addEventListener('click', () => {
    if (grantsPage < totalPages) { grantsPage++; renderGrantsPage(); }
  });
}

function renderGrantsPage() {
  const tbody = document.getElementById('access-grants-tbody');
  const total = grantsFiltered.length;
  const totalPages = Math.max(1, Math.ceil(total / GRANTS_PAGE_SIZE));
  grantsPage = Math.min(Math.max(grantsPage, 1), totalPages);
  const start = (grantsPage - 1) * GRANTS_PAGE_SIZE;
  const pageRows = grantsFiltered.slice(start, start + GRANTS_PAGE_SIZE);
  if (!pageRows.length) {
    tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--muted);">No grants match your search.</td></tr>';
  } else {
    tbody.innerHTML = pageRows.map(g => `
      <tr>
        <td>${escapeHtml(g.grantee)}</td>
        <td><span class="kind-badge ${escapeHtml(g.level)}">${escapeHtml(g.level)}</span></td>
        <td>${escapeHtml(g.object)}</td>
        <td>${escapeHtml(g.privilege)}</td>
      </tr>
    `).join('');
  }
  document.getElementById('access-grants-count').textContent = total;
  document.getElementById('access-grants-foot').textContent =
    `Showing ${pageRows.length ? start + 1 : 0}-${start + pageRows.length} of ${total} grants`;
  renderGrantsPager(totalPages);
}

function populateGrantsFilterOptions() {
  const levelSel = document.getElementById('access-grants-filter-level');
  const privSel = document.getElementById('access-grants-filter-privilege');
  const currentLevel = levelSel.value;
  const currentPriv = privSel.value;
  const levels = [...new Set(accessGrantsData.map(g => g.level))].sort();
  const privileges = [...new Set(accessGrantsData.map(g => g.privilege))].sort();
  levelSel.innerHTML = '<option value="">All levels</option>' +
    levels.map(l => `<option value="${escapeHtml(l)}">${escapeHtml(l)}</option>`).join('');
  privSel.innerHTML = '<option value="">All privileges</option>' +
    privileges.map(p => `<option value="${escapeHtml(p)}">${escapeHtml(p)}</option>`).join('');
  levelSel.value = levels.includes(currentLevel) ? currentLevel : '';
  privSel.value = privileges.includes(currentPriv) ? currentPriv : '';
}

function updateGrantsFilterButtonState() {
  const active = document.getElementById('access-grants-filter-level').value ||
    document.getElementById('access-grants-filter-object').value.trim() ||
    document.getElementById('access-grants-filter-privilege').value;
  document.getElementById('access-grants-filter-btn').classList.toggle('solid', !!active);
}

function applyGrantsSearch(term) {
  const needle = (term || '').trim().toLowerCase();
  const levelFilter = document.getElementById('access-grants-filter-level').value;
  const objectFilter = document.getElementById('access-grants-filter-object').value.trim().toLowerCase();
  const privilegeFilter = document.getElementById('access-grants-filter-privilege').value;
  grantsFiltered = accessGrantsData.filter(g =>
    (!needle ||
      g.grantee.toLowerCase().includes(needle) ||
      g.object.toLowerCase().includes(needle) ||
      g.privilege.toLowerCase().includes(needle) ||
      g.level.toLowerCase().includes(needle)
    ) &&
    (!levelFilter || g.level === levelFilter) &&
    (!objectFilter || g.object.toLowerCase().includes(objectFilter)) &&
    (!privilegeFilter || g.privilege === privilegeFilter)
  );
  grantsPage = 1;
  renderGrantsPage();
}

let grantsSearchTimer;
document.getElementById('access-grants-search').addEventListener('input', e => {
  clearTimeout(grantsSearchTimer);
  grantsSearchTimer = setTimeout(() => applyGrantsSearch(e.target.value), 200);
});

const grantsFilterBtn = document.getElementById('access-grants-filter-btn');
const grantsFilterPanel = document.getElementById('access-grants-filter-panel');
grantsFilterBtn.addEventListener('click', e => {
  e.stopPropagation();
  grantsFilterPanel.classList.toggle('hidden');
});
document.addEventListener('click', e => {
  if (!grantsFilterPanel.classList.contains('hidden') && !grantsFilterPanel.contains(e.target) && !grantsFilterBtn.contains(e.target)) {
    grantsFilterPanel.classList.add('hidden');
  }
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') grantsFilterPanel.classList.add('hidden');
});
document.getElementById('access-grants-filter-level').addEventListener('change', () => {
  updateGrantsFilterButtonState();
  applyGrantsSearch(document.getElementById('access-grants-search').value);
});
document.getElementById('access-grants-filter-privilege').addEventListener('change', () => {
  updateGrantsFilterButtonState();
  applyGrantsSearch(document.getElementById('access-grants-search').value);
});
let grantsObjectFilterTimer;
document.getElementById('access-grants-filter-object').addEventListener('input', () => {
  clearTimeout(grantsObjectFilterTimer);
  grantsObjectFilterTimer = setTimeout(() => {
    updateGrantsFilterButtonState();
    applyGrantsSearch(document.getElementById('access-grants-search').value);
  }, 200);
});
document.getElementById('access-grants-filter-clear').addEventListener('click', () => {
  document.getElementById('access-grants-filter-level').value = '';
  document.getElementById('access-grants-filter-object').value = '';
  document.getElementById('access-grants-filter-privilege').value = '';
  updateGrantsFilterButtonState();
  applyGrantsSearch(document.getElementById('access-grants-search').value);
  grantsFilterPanel.classList.add('hidden');
});

// ---- Effective access tab — resolved server-side per selected user ----
async function loadEffectiveAccess(userEmail) {
  const tbody = document.getElementById('access-effective-tbody');
  const foot = document.getElementById('access-effective-foot');
  if (!userEmail) { tbody.innerHTML = ''; foot.textContent = ''; return; }
  try {
    const data = await getEffectiveAccess(userEmail);
    const rows = data.effective_grants;
    foot.textContent = `${rows.length} effective grant${rows.length === 1 ? '' : 's'} for ${userEmail}`;
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--muted);">No access found for this user.</td></tr>';
      return;
    }
    tbody.innerHTML = rows.map(g => `
      <tr>
        <td>${escapeHtml(g.object)}</td>
        <td><span class="kind-badge ${escapeHtml(g.level)}">${escapeHtml(g.level)}</span></td>
        <td>${escapeHtml(g.privilege)}</td>
        <td>${escapeHtml(g.via)}</td>
      </tr>
    `).join('');
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="4" style="text-align:center;color:var(--red);">Could not load effective access: ${escapeHtml(err.message)}</td></tr>`;
  }
}

function renderEffectiveOptions() {
  const select = document.getElementById('access-effective-user');
  const current = select.value;
  select.innerHTML = accessUsersData
    .slice()
    .sort((a, b) => a.user.localeCompare(b.user))
    .map(u => `<option value="${escapeHtml(u.user)}">${escapeHtml(u.user)}</option>`)
    .join('');
  if (current && accessUsersData.some(u => u.user === current)) select.value = current;
  loadEffectiveAccess(select.value);
}

document.getElementById('access-effective-user').addEventListener('change', e => loadEffectiveAccess(e.target.value));

function renderAccessColumns(columns) {
  if (!columns || !columns.length) return '–';
  const text = columns.map(c => `${c.column} (${c.tag})`).join(', ');
  return `<span title="${escapeHtml(text)}">${escapeHtml(text)}</span>`;
}

// ---- Users tab — user access by catalog/table/column ----
async function loadUserCatalogAccess(userEmail) {
  const tbody = document.getElementById('access-user-catalog-tbody');
  const foot = document.getElementById('access-user-catalog-foot');
  if (!userEmail) { tbody.innerHTML = ''; foot.textContent = ''; return; }
  tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--muted);">Loading…</td></tr>';
  try {
    const data = await getUserCatalogAccess(userEmail);
    const rows = data.access;
    foot.textContent = `${rows.length} accessible object${rows.length === 1 ? '' : 's'} for ${userEmail}`;
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--muted);">No catalog/table access found for this user.</td></tr>';
      return;
    }
    tbody.innerHTML = rows.map(r => `
      <tr>
        <td>${escapeHtml(r.catalog)}</td>
        <td>${escapeHtml(r.schema || '–')}</td>
        <td>${escapeHtml(r.table || '–')}</td>
        <td><span class="kind-badge ${escapeHtml(r.kind)}">${escapeHtml(r.kind)}</span></td>
        <td>${escapeHtml(r.privilege)}</td>
        <td>${escapeHtml(r.via)}</td>
        <td>${renderAccessColumns(r.columns)}</td>
      </tr>
    `).join('');
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;color:var(--red);">Could not load access: ${escapeHtml(err.message)}</td></tr>`;
  }
}

function renderUserCatalogAccessOptions() {
  const select = document.getElementById('access-user-catalog-select');
  const current = select.value;
  select.innerHTML = accessUsersData
    .slice()
    .sort((a, b) => a.user.localeCompare(b.user))
    .map(u => `<option value="${escapeHtml(u.user)}">${escapeHtml(u.user)}</option>`)
    .join('');
  if (current && accessUsersData.some(u => u.user === current)) select.value = current;
  loadUserCatalogAccess(select.value);
}

document.getElementById('access-user-catalog-select').addEventListener('change', e => loadUserCatalogAccess(e.target.value));

// ---- Inspect tab — per-catalog user/column access, opened from Catalog explorer ----
async function loadCatalogInspect(catalog) {
  const tbody = document.getElementById('access-inspect-tbody');
  const foot = document.getElementById('access-inspect-foot');
  if (!catalog) { tbody.innerHTML = ''; foot.textContent = 'Pick a catalog to inspect, or use "Inspect" from Catalog explorer.'; return; }
  tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:var(--muted);">Loading…</td></tr>';
  try {
    const data = await getCatalogInspect(catalog);
    const users = data.users;
    const totalRows = users.reduce((n, u) => n + u.access.length, 0);
    foot.textContent = `${users.length} user${users.length === 1 ? '' : 's'}, ${totalRows} accessible object${totalRows === 1 ? '' : 's'} in ${catalog}`;
    if (!users.length) {
      tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:var(--muted);">No users have access to this catalog.</td></tr>';
      return;
    }
    tbody.innerHTML = users.flatMap(u => u.access.map((r, i) => `
      <tr>
        <td>${i === 0 ? escapeHtml(u.user) : ''}</td>
        <td>${i === 0 ? escapeHtml(u.groups.join(', ') || '–') : ''}</td>
        <td>${escapeHtml(r.schema || '–')}</td>
        <td>${escapeHtml(r.table || '–')}</td>
        <td><span class="kind-badge ${escapeHtml(r.kind)}">${escapeHtml(r.kind)}</span></td>
        <td>${escapeHtml(r.privilege)}</td>
        <td>${escapeHtml(r.via)}</td>
        <td>${renderAccessColumns(r.columns)}</td>
      </tr>
    `).join('')).join('');
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="8" style="text-align:center;color:var(--red);">Could not load catalog access: ${escapeHtml(err.message)}</td></tr>`;
  }
}

async function populateInspectCatalogOptions(preselect) {
  const sel = document.getElementById('access-inspect-catalog-select');
  try {
    const { catalogs } = await getCatalogTree();
    const current = preselect || sel.value;
    sel.innerHTML = '<option value="">Select a catalog…</option>' +
      catalogs.map(c => `<option value="${escapeHtml(c.catalog)}">${escapeHtml(c.catalog)}</option>`).join('');
    if (current && catalogs.some(c => c.catalog === current)) sel.value = current;
  } catch (err) {
    // Nice-to-have; leave the dropdown at its placeholder if this fails.
  }
  loadCatalogInspect(sel.value);
}

document.getElementById('access-inspect-catalog-select').addEventListener('change', e => loadCatalogInspect(e.target.value));

// Opened from Catalog explorer's "Inspect" button (see renderCatalogTree).
function inspectCatalog(catalogName) {
  navigateTo('access');
  document.querySelector('#page-access .tab[data-tab="inspect"]').click();
  populateInspectCatalogOptions(catalogName);
}

// ---- Tab switching ----
document.querySelectorAll('#page-access .tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('#page-access .tab').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    document.querySelectorAll('#page-access .access-subpage').forEach(p => p.classList.add('hidden'));
    document.getElementById('access-tab-' + tab.dataset.tab).classList.remove('hidden');
  });
});

async function loadAccessGovernance() {
  setAccessError(null);
  try {
    const [stats, users, mostActive, highRisk, groups, grants] = await Promise.all([
      getAccessStats(), getAccessUsers(), getMostActiveUsers(), getHighRiskUsers(), getAccessGroups(), getAccessGrants(),
    ]);
    accessUsersData = users.users;
    accessGroupsData = groups.groups;
    accessGrantsData = grants.grants;

    document.getElementById('access-stat-users').textContent = stats.total_users;
    document.getElementById('access-stat-groups').textContent = stats.total_groups;
    document.getElementById('access-stat-grants').textContent = stats.total_grants;
    document.getElementById('access-stat-highrisk').textContent = stats.high_risk_users;

    applyAccessSearch(document.getElementById('access-search').value);
    renderMostActive(mostActive.most_active);
    renderHighRisk(highRisk.high_risk);
    renderGroups(accessGroupsData, document.getElementById('access-groups-search').value);
    populateGrantsFilterOptions();
    applyGrantsSearch(document.getElementById('access-grants-search').value);
    renderEffectiveOptions();
    renderUserCatalogAccessOptions();
    populateInspectCatalogOptions();
    markLiveUpdated('access');
  } catch (err) {
    setAccessError(
      `Could not reach the Databricks backend at ${CATALOG_API_BASE} (${err.message}). ` +
      `Make sure "python main.py" is running in backend/.`
    );
    markLiveStale('access');
    document.getElementById('access-users-tbody').innerHTML = '';
    document.getElementById('access-most-active').innerHTML = '';
    document.getElementById('access-highrisk-tbody').innerHTML = '';
    document.getElementById('access-groups-tbody').innerHTML = '';
    document.getElementById('access-grants-tbody').innerHTML = '';
    document.getElementById('access-effective-tbody').innerHTML = '';
    document.getElementById('access-user-catalog-tbody').innerHTML = '';
    document.getElementById('access-inspect-tbody').innerHTML = '';
  }
}

let accessSearchTimer;
document.getElementById('access-search').addEventListener('input', e => {
  clearTimeout(accessSearchTimer);
  accessSearchTimer = setTimeout(() => applyAccessSearch(e.target.value), 200);
});
document.getElementById('access-refresh').addEventListener('click', e => {
  e.preventDefault();
  withLoading(loadAccessGovernance);
});

startLiveRefresh('access', loadAccessGovernance);

// ---------------------------------------------------------------------
// Job intelligence — one fetch per panel (stats, run status, failures by
// cause, runs trend, job runs table, job details drawer). Live data from
// system.lakeflow.job_run_timeline + the Jobs REST API (see
// backend/job_intelligence/*.py) — the job details drawer generates a
// real AI root cause analysis on demand via a model serving endpoint.
//
// The jobs table fetches every run in the window (not just failures),
// but the Status filter defaults to "Failed" so the table only shows
// failures out of the box — picking Success/Cancelled from that filter
// widens the view.
// ---------------------------------------------------------------------
const JOBS_DEFAULT_STATUS_FILTER = 'Failed';
let jobsRunsData = [];

function setJobsError(message) {
  const el = document.getElementById('jobs-error');
  if (message) { el.textContent = message; el.classList.remove('hidden'); }
  else el.classList.add('hidden');
}

function tagClass(tag) {
  const map = { Finance: 'finance', Healthcare: 'healthcare', Retail: 'retail', Marketing: 'marketing', 'Supply Chain': 'supply' };
  return map[tag] || 'finance';
}

function statusBadgeClass(status) {
  const s = (status || '').toLowerCase();
  if (s.startsWith('success')) return 'success';
  if (s.startsWith('cancel')) return 'cancelled';
  return 'failed';
}

function renderFailuresByCause(causes) {
  const container = document.getElementById('jobs-failure-causes');
  if (!causes.length) {
    container.innerHTML = '<div style="color:var(--muted);font-size:12.5px;">No failure data.</div>';
    return;
  }
  const max = causes[0].count || 1;
  container.innerHTML = causes.map(c => `
    <div class="hbar-row">
      <span class="hbar-label" title="${escapeHtml(c.cause)}">${escapeHtml(c.cause)}</span>
      <div class="hbar-track"><div class="hbar-fill" style="width:${Math.round((c.count / max) * 100)}%;background:var(--red);"></div></div>
      <span class="hbar-value">${c.count}</span>
    </div>
  `).join('');
}

function populateJobFilterOptions(jobs) {
  const tagSel = document.getElementById('jobs-filter-tag');
  const statusSel = document.getElementById('jobs-filter-status');
  const firstRun = !tagSel.dataset.populated;

  const fillSelect = (sel, values, allLabel, defaultValue) => {
    const wanted = sel.dataset.populated ? sel.value : (defaultValue ?? '');
    sel.innerHTML = `<option value="">${allLabel}</option>` +
      values.map(v => `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join('');
    sel.value = values.includes(wanted) || wanted === '' ? wanted : '';
    sel.dataset.populated = 'true';
  };
  fillSelect(tagSel, [...new Set(jobs.map(j => j.tag))].sort(), 'All tags');
  fillSelect(statusSel, [...new Set(jobs.map(j => j.status))].sort(), 'All status', JOBS_DEFAULT_STATUS_FILTER);
  if (firstRun) updateJobsFilterButtonState();
}

function updateJobsFilterButtonState() {
  const tag = document.getElementById('jobs-filter-tag').value;
  const status = document.getElementById('jobs-filter-status').value;
  const active = !!tag || status !== JOBS_DEFAULT_STATUS_FILTER;
  document.getElementById('jobs-filter-btn').classList.toggle('solid', active);
}

function renderFailedJobsTable(jobs, term) {
  const tbody = document.getElementById('jobs-failed-tbody');
  const needle = (term || '').trim().toLowerCase();
  const tagFilter = document.getElementById('jobs-filter-tag').value;
  const statusFilter = document.getElementById('jobs-filter-status').value;
  const filtered = jobs.filter(j =>
    (!needle || j.job_name.toLowerCase().includes(needle) || j.tag.toLowerCase().includes(needle)) &&
    (!tagFilter || j.tag === tagFilter) &&
    (!statusFilter || j.status === statusFilter)
  );
  if (!filtered.length) {
    tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:var(--muted);">No jobs match your filters.</td></tr>';
  } else {
    tbody.innerHTML = filtered.map(j => `
      <tr>
        <td><input type="checkbox"></td>
        <td class="link-cell" data-job="${escapeHtml(j.job_id)}">${highlight(j.job_name, needle)}</td>
        <td><span class="badge ${tagClass(j.tag)}">${escapeHtml(j.tag)}</span></td>
        <td><span class="badge ${statusBadgeClass(j.status)}">${escapeHtml(j.status)}</span></td>
        <td>${escapeHtml(j.last_run)}</td>
        <td>${escapeHtml(j.duration)}</td>
        <td>${j.rca_status === '—' ? '—' : `<span class="badge ${j.rca_status === 'Completed' ? 'completed' : 'medium'}">${escapeHtml(j.rca_status)}</span>`}</td>
        <td><a href="#" class="view-link" data-job="${escapeHtml(j.job_id)}">View details</a> ⋮</td>
      </tr>
    `).join('');
    tbody.querySelectorAll('[data-job]').forEach(el => {
      el.style.cursor = 'pointer';
      el.addEventListener('click', e => { e.preventDefault(); openJobDrawer(el.dataset.job); });
    });
  }
  document.getElementById('jobs-failed-foot').textContent = `Showing ${filtered.length} of ${jobs.length} runs`;
}

function renderJobDrawer(details) {
  const o = details.overview;
  document.getElementById('jobs-drawer-overview').innerHTML = `
    <tr><td>Job name</td><td><b>${escapeHtml(o.job_name)}</b></td></tr>
    <tr><td>Status</td><td><span class="badge ${statusBadgeClass(o.status)}">${escapeHtml(o.status)}</span></td></tr>
    <tr><td>Tag / Project</td><td><span class="badge ${tagClass(o.tag)}">${escapeHtml(o.tag)}</span></td></tr>
    <tr><td>Failed at</td><td><b>${escapeHtml(o.failed_at)}</b></td></tr>
    <tr><td>Run ID</td><td><b>${escapeHtml(o.run_id)}</b></td></tr>
    <tr><td>Duration</td><td><b>${escapeHtml(o.duration)}</b></td></tr>
  `;
  document.getElementById('jobs-drawer-confidence').textContent = `${details.root_cause.confidence}% confidence`;
  document.getElementById('jobs-drawer-rootcause').textContent = details.root_cause.summary;
  document.getElementById('jobs-drawer-stacktrace').innerHTML = details.stack_trace.map(escapeHtml).join('<br>');
  document.getElementById('jobs-drawer-actions').innerHTML = details.recommended_actions.map((a, i) => `
    <tr>
      <td>${i + 1}</td>
      <td><b>${escapeHtml(a.title)}</b>${a.description ? `<span>${escapeHtml(a.description)}</span>` : ''}</td>
    </tr>
  `).join('');
}

function closeJobDrawer() {
  document.getElementById('jobDrawer').classList.add('hidden');
  document.getElementById('jobDrawerBackdrop').classList.add('hidden');
}

async function openJobDrawer(jobId) {
  document.getElementById('jobDrawerBackdrop').classList.remove('hidden');
  document.getElementById('jobDrawer').classList.remove('hidden');
  document.getElementById('jobDrawerLoading').classList.remove('hidden');
  document.getElementById('jobDrawerBody').classList.add('hidden');
  try {
    const details = await getJobDetails(jobId);
    renderJobDrawer(details);
    document.getElementById('jobDrawerLoading').classList.add('hidden');
    document.getElementById('jobDrawerBody').classList.remove('hidden');
  } catch (err) {
    document.getElementById('jobDrawerLoading').classList.add('hidden');
    document.getElementById('jobDrawerBody').classList.remove('hidden');
    document.getElementById('jobs-drawer-rootcause').textContent = `Could not load job details: ${err.message}`;
  }
}

document.getElementById('closeDrawer').addEventListener('click', closeJobDrawer);
document.getElementById('jobDrawerBackdrop').addEventListener('click', closeJobDrawer);
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && !document.getElementById('jobDrawer').classList.contains('hidden')) closeJobDrawer();
});

async function loadJobIntelligence() {
  setJobsError(null);
  try {
    const [stats, runStatus, causes, trend, runs] = await Promise.all([
      getJobsStats(), getJobRunStatus(), getFailuresByCause(), getJobsRunsTrend(), getJobRuns(),
    ]);
    jobsRunsData = runs.jobs;
    populateJobFilterOptions(jobsRunsData);

    document.getElementById('jobs-stat-total').textContent = stats.total_runs;
    document.getElementById('jobs-stat-success').textContent = stats.successful_runs;
    document.getElementById('jobs-stat-failed').textContent = stats.failed_runs;
    document.getElementById('jobs-stat-running').textContent = stats.running_jobs;
    document.getElementById('jobs-stat-rca').textContent = stats.rca_generated;

    renderRunStatusDonut(
      document.getElementById('jobs-runstatus-donut'),
      document.getElementById('jobs-runstatus-total'),
      document.getElementById('jobs-runstatus-legend'),
      runStatus
    );
    renderFailuresByCause(causes.causes);
    renderTrendChart(document.getElementById('jobs-trend-svg'), trend.points, null);
    renderFailedJobsTable(jobsRunsData, document.getElementById('jobs-search').value);

    markLiveUpdated('jobs');
  } catch (err) {
    setJobsError(
      `Could not reach the Databricks backend at ${CATALOG_API_BASE} (${err.message}). ` +
      `Make sure "python main.py" is running in backend/.`
    );
    markLiveStale('jobs');
  }
}

const jobsFilterBtn = document.getElementById('jobs-filter-btn');
const jobsFilterPanel = document.getElementById('jobs-filter-panel');

jobsFilterBtn.addEventListener('click', e => {
  e.stopPropagation();
  jobsFilterPanel.classList.toggle('hidden');
});
document.addEventListener('click', e => {
  if (!jobsFilterPanel.classList.contains('hidden') && !jobsFilterPanel.contains(e.target) && !jobsFilterBtn.contains(e.target)) {
    jobsFilterPanel.classList.add('hidden');
  }
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') jobsFilterPanel.classList.add('hidden');
});

['jobs-filter-tag', 'jobs-filter-status'].forEach(id => {
  document.getElementById(id).addEventListener('change', () => {
    updateJobsFilterButtonState();
    renderFailedJobsTable(jobsRunsData, document.getElementById('jobs-search').value);
  });
});
document.getElementById('jobs-filter-clear').addEventListener('click', () => {
  document.getElementById('jobs-filter-tag').value = '';
  document.getElementById('jobs-filter-status').value = JOBS_DEFAULT_STATUS_FILTER;
  updateJobsFilterButtonState();
  renderFailedJobsTable(jobsRunsData, document.getElementById('jobs-search').value);
  jobsFilterPanel.classList.add('hidden');
});

let jobsSearchTimer;
document.getElementById('jobs-search').addEventListener('input', e => {
  clearTimeout(jobsSearchTimer);
  jobsSearchTimer = setTimeout(() => renderFailedJobsTable(jobsRunsData, e.target.value), 200);
});

startLiveRefresh('jobs', loadJobIntelligence);

// ---------------------------------------------------------------------
// Data security — one fetch per panel (stats, risk distribution,
// category breakdown, top tables, sensitive tables, recent
// classifications)
// ---------------------------------------------------------------------
let sensitiveTablesData = [];
// Persisted across re-renders (search filtering, 30s live auto-refresh) so
// expanded access rows/groups don't silently snap shut on the next redraw.
const secExpandedTables = new Set(); // table paths with the access drill-down open
const secExpandedGroups = new Set(); // `${table}::${group}` keys with the member list open
const RISK_COLORS = { high: 'var(--red)', medium: 'var(--amber)', low: '#a9c8f5' };

function setSecurityError(message) {
  const el = document.getElementById('security-error');
  if (message) { el.textContent = message; el.classList.remove('hidden'); }
  else el.classList.add('hidden');
}

function securityRiskBadge(level) {
  const cls = level === 'high' ? 'high' : level === 'medium' ? 'medium' : 'low';
  const label = level ? level.charAt(0).toUpperCase() + level.slice(1) : 'Low';
  return `<span class="badge ${cls}">${label}</span>`;
}

function renderRiskDonut(riskBreakdown) {
  const total = (riskBreakdown.high || 0) + (riskBreakdown.medium || 0) + (riskBreakdown.low || 0);
  document.getElementById('sec-risk-total').textContent = total;
  const donut = document.getElementById('sec-risk-donut');
  const legend = document.getElementById('sec-risk-legend');
  if (!total) {
    donut.style.background = '#eceef2';
    legend.innerHTML = '<div style="color:var(--muted);font-size:12.5px;">No PII columns detected.</div>';
    return;
  }
  let angle = 0;
  const stops = [];
  const legendRows = [];
  for (const level of ['high', 'medium', 'low']) {
    const count = riskBreakdown[level] || 0;
    if (!count) continue;
    const sweep = (count / total) * 360;
    stops.push(`${RISK_COLORS[level]} ${angle}deg ${angle + sweep}deg`);
    legendRows.push(`<div class="legend-row"><span class="dot" style="background:${RISK_COLORS[level]}"></span>${level.charAt(0).toUpperCase() + level.slice(1)} Risk<br>${count} (${(count / total * 100).toFixed(1)}%)</div>`);
    angle += sweep;
  }
  donut.style.background = `conic-gradient(${stops.join(', ')})`;
  legend.innerHTML = legendRows.join('');
}

function renderTopTables(topTables) {
  const container = document.getElementById('sec-top-tables');
  if (!topTables.length) {
    container.innerHTML = '<div style="color:var(--muted);font-size:12.5px;">No PII columns detected.</div>';
    return;
  }
  const max = topTables[0].count || 1;
  container.innerHTML = topTables.map(t => `
    <div class="hbar-row">
      <span class="hbar-label" title="${escapeHtml(t.table)}">${escapeHtml(t.table)}</span>
      <div class="hbar-track"><div class="hbar-fill" style="width:${Math.round((t.count / max) * 100)}%;background:#8b6cf0;"></div></div>
      <span class="hbar-value">${t.count}</span>
    </div>
  `).join('');
}

function renderAccessDetail(detail, tableKey) {
  const groups = (detail && detail.groups) || [];
  const users = (detail && detail.users) || [];
  if (!groups.length && !users.length) {
    return '<div class="access-detail-empty">No direct grants found for this table.</div>';
  }
  let html = '';
  if (groups.length) {
    html += '<div class="access-detail-subhead">Group view</div>';
    html += groups.map(g => {
      const gkey = `${tableKey}::${g.group}`;
      const isOpen = secExpandedGroups.has(gkey);
      return `
      <div class="access-detail-group${isOpen ? ' open' : ''}" data-gkey="${escapeHtml(gkey)}">
        <div class="access-detail-group-head">
          <svg class="tree-chevron" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M9 6l6 6-6 6"/></svg>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="9" cy="8" r="3.2"/><path d="M3 20c0-3.3 2.7-5.5 6-5.5s6 2.2 6 5.5"/><circle cx="18" cy="8.5" r="2.4"/><path d="M15.5 14.6c2.5.3 4.5 2.2 4.5 5.4"/></svg>
          <span class="access-detail-name">${escapeHtml(g.group)}</span>
          <span class="tree-meta">${g.member_count} member${g.member_count === 1 ? '' : 's'}</span>
        </div>
        <div class="access-detail-members">
          ${g.members && g.members.length
            ? g.members.map(m => `
                <div class="access-detail-member">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="8" r="4"/><path d="M4 20c0-4.4 3.6-7 8-7s8 2.6 8 7"/></svg>
                  ${escapeHtml(m)}
                </div>`).join('')
            : '<div class="access-detail-empty">No members found for this group.</div>'}
        </div>
      </div>
    `;
    }).join('');
  }
  if (users.length) {
    html += '<div class="access-detail-subhead">Direct user access</div>';
    html += users.map(u => `
      <div class="access-detail-member">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="8" r="4"/><path d="M4 20c0-4.4 3.6-7 8-7s8 2.6 8 7"/></svg>
        ${escapeHtml(u)}
      </div>
    `).join('');
  }
  return html;
}

function renderSensitiveDetail(t) {
  const summaryHtml = t.ai_summary
    ? `<div class="access-detail-subhead">AI summary (ai_query)</div>
       <div style="font-size:12.5px;color:var(--muted);margin:0 0 10px;">${escapeHtml(t.ai_summary)}</div>`
    : '';
  return `
    ${summaryHtml}
    <div class="access-detail-subhead">Access</div>
    ${renderAccessDetail(t.access_detail, t.table)}
  `;
}

function renderSensitiveTables(tables, term) {
  const tbody = document.getElementById('sec-tables-tbody');
  document.getElementById('sec-tables-count').textContent = tables.length;
  if (!tables.length) {
    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--muted);">No tables match your search.</td></tr>';
  } else {
    tbody.innerHTML = tables.map((t, i) => {
      const isOpen = secExpandedTables.has(t.table);
      return `
      <tr>
        <td class="link-cell">${highlight(t.table, term)}</td>
        <td>${escapeHtml(t.pii_columns.join(', '))}</td>
        <td>${escapeHtml(t.data_types.join(', '))}</td>
        <td>${(t.tags && t.tags.length) ? escapeHtml(t.tags.join(', ')) : '–'}</td>
        <td>
          <button class="access-expand-btn${isOpen ? ' open' : ''}" data-idx="${i}" data-table="${escapeHtml(t.table)}">
            <svg class="tree-chevron" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M9 6l6 6-6 6"/></svg>
            ${escapeHtml(t.access)}
          </button>
        </td>
        <td>${securityRiskBadge(t.risk)}</td>
        <td>${formatDate(t.last_altered)}</td>
      </tr>
      <tr class="sens-detail-row${isOpen ? '' : ' hidden'}" data-detail-idx="${i}"><td colspan="7">${renderSensitiveDetail(t)}</td></tr>
    `;
    }).join('');

    tbody.querySelectorAll('.access-expand-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const idx = btn.dataset.idx;
        const detailRow = tbody.querySelector(`tr[data-detail-idx="${idx}"]`);
        detailRow.classList.toggle('hidden');
        btn.classList.toggle('open');
        if (btn.classList.contains('open')) secExpandedTables.add(btn.dataset.table);
        else secExpandedTables.delete(btn.dataset.table);
      });
    });
    tbody.querySelectorAll('.access-detail-group-head').forEach(head => {
      head.addEventListener('click', () => {
        const groupEl = head.parentElement;
        groupEl.classList.toggle('open');
        if (groupEl.classList.contains('open')) secExpandedGroups.add(groupEl.dataset.gkey);
        else secExpandedGroups.delete(groupEl.dataset.gkey);
      });
    });
  }
  document.getElementById('sec-tables-foot').textContent = `Showing ${tables.length} of ${sensitiveTablesData.length} tables`;
}

function populateSecTagOptions(tables) {
  const sel = document.getElementById('sec-filter-tag');
  const tags = [...new Set(tables.flatMap(t => t.tags || []))].sort();
  const current = sel.value;
  sel.innerHTML = '<option value="">All tags</option>' +
    tags.map(t => `<option value="${escapeHtml(t)}">${escapeHtml(t)}</option>`).join('');
  sel.value = tags.includes(current) ? current : '';
}

function updateSecFilterButtonState() {
  document.getElementById('sec-filter-btn').classList.toggle('solid', !!document.getElementById('sec-filter-tag').value);
}

function applySecuritySearch(term) {
  const needle = (term || '').trim().toLowerCase();
  const tagFilter = document.getElementById('sec-filter-tag').value;
  const tables = sensitiveTablesData.filter(t =>
    (!needle ||
      t.table.toLowerCase().includes(needle) ||
      t.pii_columns.some(c => c.toLowerCase().includes(needle)) ||
      t.data_types.some(d => d.toLowerCase().includes(needle))
    ) &&
    (!tagFilter || (t.tags || []).includes(tagFilter))
  );
  renderSensitiveTables(tables, needle);
}

function renderRecentClassifications(recent) {
  const tbody = document.getElementById('sec-recent-tbody');
  if (!recent.length) {
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--muted);">No PII columns detected.</td></tr>';
    return;
  }
  tbody.innerHTML = recent.map(c => `
    <tr>
      <td>${escapeHtml(c.table_path)}</td>
      <td>${escapeHtml(c.column)}</td>
      <td>PII (${escapeHtml(c.category)})</td>
      <td>${c.confidence}%</td>
      <td>${securityRiskBadge(c.risk)}</td>
      <td>${formatDate(c.last_altered)}</td>
    </tr>
  `).join('');
}

async function loadDataSecurity() {
  setSecurityError(null);
  try {
    const [stats, riskDist, categoryBreakdown, topTables, sensitiveTables, recent] = await Promise.all([
      getSecurityStats(), getRiskDistribution(), getSecurityCategoryBreakdown(),
      getTopPiiTables(), getSensitiveTables(), getRecentClassifications(),
    ]);
    sensitiveTablesData = sensitiveTables.sensitive_tables;
    populateSecTagOptions(sensitiveTablesData);

    document.getElementById('sec-stat-tables').textContent = stats.tables_with_pii;
    document.getElementById('sec-stat-highrisk').textContent = stats.high_risk_tables;
    document.getElementById('sec-stat-columns').textContent = stats.pii_columns;
    document.getElementById('sec-stat-classified').textContent = stats.classified_datasets;
    renderRiskDonut(riskDist.risk_breakdown);
    renderCategoryBars(document.getElementById('sec-category-bars'), categoryBreakdown.category_breakdown);
    renderTopTables(topTables.top_tables);
    applySecuritySearch(document.getElementById('sec-search').value);
    renderRecentClassifications(recent.recent_classifications);
    markLiveUpdated('sec');
  } catch (err) {
    setSecurityError(
      `Could not reach the Databricks backend at ${CATALOG_API_BASE} (${err.message}). ` +
      `Make sure "python main.py" is running in backend/.`
    );
    markLiveStale('sec');
    document.getElementById('sec-tables-tbody').innerHTML = '';
    document.getElementById('sec-recent-tbody').innerHTML = '';
  }
}

let securitySearchTimer;
document.getElementById('sec-search').addEventListener('input', e => {
  clearTimeout(securitySearchTimer);
  securitySearchTimer = setTimeout(() => applySecuritySearch(e.target.value), 200);
});
document.getElementById('sec-refresh').addEventListener('click', e => {
  e.preventDefault();
  withLoading(loadDataSecurity);
});

const secFilterBtn = document.getElementById('sec-filter-btn');
const secFilterPanel = document.getElementById('sec-filter-panel');
secFilterBtn.addEventListener('click', e => {
  e.stopPropagation();
  secFilterPanel.classList.toggle('hidden');
});
document.addEventListener('click', e => {
  if (!secFilterPanel.classList.contains('hidden') && !secFilterPanel.contains(e.target) && !secFilterBtn.contains(e.target)) {
    secFilterPanel.classList.add('hidden');
  }
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') secFilterPanel.classList.add('hidden');
});
document.getElementById('sec-filter-tag').addEventListener('change', () => {
  updateSecFilterButtonState();
  applySecuritySearch(document.getElementById('sec-search').value);
});
document.getElementById('sec-filter-clear').addEventListener('click', () => {
  document.getElementById('sec-filter-tag').value = '';
  updateSecFilterButtonState();
  applySecuritySearch(document.getElementById('sec-search').value);
  secFilterPanel.classList.add('hidden');
});

startLiveRefresh('security', loadDataSecurity);
