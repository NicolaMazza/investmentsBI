// InvestmentsBI — dashboard app
// State lives in the URL hash: #dimension=sector&segment=Technology&date=2026-05-22
// On every hash change, the full render pipeline reruns.

// ── Dimensions ───────────────────────────────────────────────────────────────

const DIMENSIONS = [
  { key: 'sector',     label: 'Sector',     chartType: 'treemap', available: true  },
  { key: 'company',    label: 'Company',    chartType: 'treemap', available: true  },
  { key: 'country',    label: 'Country',    chartType: 'treemap', available: true  },
  { key: 'currency',   label: 'Currency',   chartType: 'donut',   available: true  },
  { key: 'market_cap', label: 'Market cap', chartType: 'bar',     available: false },
  { key: 'product',    label: 'ETF',        chartType: 'treemap', available: true  },
];

const JOBS = [
  { key: 'ishares_holdings',  label: 'iShares holdings' },
  { key: 'etf_holdings',      label: 'Vanguard + HSBC holdings' },
  { key: 'position_snapshot', label: 'Position snapshot' },
];

// ── State ─────────────────────────────────────────────────────────────────────

function getState() {
  const p = new URLSearchParams(location.hash.slice(1));
  return {
    dimension: p.get('dimension') || 'sector',
    segment:   p.get('segment')   || null,
    date:      p.get('date')      || null,
  };
}

function pushState(updates) {
  const s = { ...getState(), ...updates };
  const p = new URLSearchParams({ dimension: s.dimension });
  if (s.segment) p.set('segment', s.segment);
  if (s.date)    p.set('date',    s.date);
  location.hash = p.toString();
}

// ── Formatting ────────────────────────────────────────────────────────────────

function fmtEUR(v) {
  if (v == null) return '—';
  return new Intl.NumberFormat('en-EU', {
    style: 'currency', currency: 'EUR', maximumFractionDigits: 0,
  }).format(v);
}

function fmtPct(w) { return (w * 100).toFixed(1) + '%'; }

// ── API calls ─────────────────────────────────────────────────────────────────

async function fetchAllocation(dimension, date) {
  const params = new URLSearchParams({ dimension });
  if (date) params.set('date', date);
  const res = await fetch(`api/allocation?${params}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function fetchDrill(dimension, segment, date) {
  const params = new URLSearchParams({ dimension, segment });
  if (date) params.set('date', date);
  const res = await fetch(`api/drill?${params}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function fetchHealth() {
  const res = await fetch('api/health');
  return res.json();
}

// ── Render: header ────────────────────────────────────────────────────────────

function renderHeader(data) {
  const sub = document.getElementById('header-subtitle');
  sub.textContent = data.as_of_date
    ? `As of ${data.as_of_date} · base EUR`
    : 'No data yet';
  document.getElementById('date-range').hidden = false;
}

// ── Render: KPIs ─────────────────────────────────────────────────────────────

function renderKPIs(data) {
  document.getElementById('kpi-total').textContent = fmtEUR(data.total_eur);
  document.getElementById('kpi-funds').textContent = data.funds ?? '—';
  document.getElementById('kpi-lookt').textContent = data.look_through ?? '—';
  document.getElementById('kpi-top').textContent   = data.top_single   ?? '—';
}

// ── Render: pivot pills ───────────────────────────────────────────────────────

function renderPivots(activeDim) {
  const chipset = document.getElementById('dimension-chips');
  chipset.innerHTML = '';
  for (const dim of DIMENSIONS) {
    const chip = document.createElement('button');
    chip.className = 'filter-chip' +
      (dim.key === activeDim ? ' selected' : '') +
      (!dim.available        ? ' disabled'  : '');
    chip.textContent = dim.label;
    chip.dataset.dim = dim.key;
    chip.disabled = !dim.available;
    if (dim.available) {
      chip.addEventListener('click', () => {
        if (dim.key !== getState().dimension) {
          pushState({ dimension: dim.key, segment: null });
        }
      });
    }
    chipset.appendChild(chip);
  }
}

// ── Render: donut chart ───────────────────────────────────────────────────────

let _donutChart = null;

function renderDonut(data, segment, onSelect) {
  const canvas = document.getElementById('donut-canvas');
  const labels  = data.rows.map(r => r.label);
  const values  = data.rows.map(r => r.value_eur);
  const colors  = data.rows.map((_, i) => TM_COLORS[i % TM_COLORS.length]);
  const alphas  = data.rows.map(r => (segment && r.label !== segment) ? 0.35 : 1);
  const bgs     = colors.map((c, i) => c + Math.round(alphas[i] * 255).toString(16).padStart(2, '0'));

  if (_donutChart) { _donutChart.destroy(); _donutChart = null; }

  _donutChart = new Chart(canvas, {
    type: 'doughnut',
    data: {
      labels,
      datasets: [{
        data: values,
        backgroundColor: bgs,
        borderColor: 'transparent',
        hoverOffset: 8,
      }],
    },
    options: {
      cutout: '60%',
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: ctx => ` ${fmtEUR(ctx.raw)}  (${fmtPct(ctx.raw / data.total_eur)})`,
          },
        },
      },
      onClick: (evt, els) => {
        if (!els.length) return;
        const lbl = labels[els[0].index];
        onSelect(lbl === segment ? null : lbl);
      },
    },
  });
}

// ── Render: main viz ──────────────────────────────────────────────────────────

function renderViz(data, segment) {
  const dim = DIMENSIONS.find(d => d.key === data.dimension) || DIMENSIONS[0];
  document.getElementById('viz-title').textContent =
    `Allocation by ${dim.label.toLowerCase()}`;

  const svg    = document.getElementById('treemap-svg');
  const canvas = document.getElementById('donut-canvas');

  if (dim.chartType === 'treemap') {
    svg.hidden    = false;
    canvas.hidden = true;
    if (_donutChart) { _donutChart.destroy(); _donutChart = null; }
    const rows = data.rows.map(r => ({ ...r, value: r.value_eur }));
    renderTreemap(svg, rows, segment, item => {
      pushState({ segment: item.label === segment ? null : item.label });
    });
  } else if (dim.chartType === 'donut') {
    svg.hidden    = true;
    canvas.hidden = false;
    renderDonut(data, segment, lbl => {
      pushState({ segment: lbl });
    });
  }
}

// ── Render: drill panel ───────────────────────────────────────────────────────

async function renderDrill(data, segment) {
  const panel = document.getElementById('drill-panel');
  const row   = document.getElementById('main-row');

  if (!segment) {
    panel.hidden = true;
    row.classList.add('no-drill');
    return;
  }

  const item = data.rows.find(r => r.label === segment);
  if (!item) { panel.hidden = true; return; }

  panel.hidden = false;
  row.classList.remove('no-drill');

  document.getElementById('drill-title').textContent = `${item.label} · drill`;
  document.getElementById('drill-content').innerHTML = `
    <div class="drill-value">${fmtPct(item.weight)}</div>
    <div class="drill-meta">${fmtEUR(item.value_eur)}</div>
    <div class="drill-loading">Loading details…</div>`;

  try {
    const { dimension, date } = getState();
    const drill = await fetchDrill(dimension, segment, date);
    document.getElementById('drill-content').innerHTML = _renderDrillContent(drill);
  } catch (err) {
    document.getElementById('drill-content').querySelector('.drill-loading')
      ?.remove();
    log.warn?.('drill fetch failed:', err);
  }
}

function _renderDrillContent(drill) {
  const heldRows = drill.held_via.map(h => `
    <div class="drill-row">
      <span>${h.name}</span>
      <span class="secondary">${fmtEUR(h.contribution_eur)}</span>
    </div>`).join('');

  const constituentRows = drill.constituents.slice(0, 10).map(c => `
    <div class="drill-row">
      <span>${c.name}</span>
      <span class="secondary">${fmtPct(c.weight_in_segment)}</span>
    </div>`).join('');

  return `
    <div class="drill-value">${fmtPct(drill.weight)}</div>
    <div class="drill-meta">${fmtEUR(drill.value_eur)}</div>

    <div class="drill-subhead">Held via</div>
    ${heldRows || '<div class="drill-row"><span class="secondary">—</span></div>'}

    <div class="drill-subhead">Top companies</div>
    ${constituentRows || '<div class="drill-row"><span class="secondary">—</span></div>'}
    ${drill.constituents.length > 10
      ? `<div class="drill-row"><span class="secondary muted">+${drill.constituents.length - 10} more</span></div>`
      : ''}`;
}

// ── Render: data table ────────────────────────────────────────────────────────

function renderTable(data) {
  const dim = DIMENSIONS.find(d => d.key === data.dimension) || DIMENSIONS[0];

  document.getElementById('table-head').innerHTML = `
    <th>${dim.label}</th>
    <th>Value EUR</th>
    <th>Portfolio %</th>
    <th>Δ 30d</th>
    <th>Holdings</th>`;

  document.getElementById('table-body').innerHTML = data.rows.map(row => `
    <tr data-segment="${row.label}">
      <td>${row.label}</td>
      <td>${fmtEUR(row.value_eur)}</td>
      <td>${fmtPct(row.weight)}</td>
      <td class="muted">—</td>
      <td class="muted">—</td>
    </tr>`).join('');

  document.querySelectorAll('#table-body tr').forEach(tr => {
    tr.addEventListener('click', () => {
      const seg = tr.dataset.segment;
      pushState({ segment: seg === getState().segment ? null : seg });
    });
  });
}

// ── Render: stub banner ───────────────────────────────────────────────────────

function renderStubBanner(data) {
  document.getElementById('stub-banner').hidden = !data.stub;
}

// ── Full render pipeline ──────────────────────────────────────────────────────

async function render() {
  const { dimension, segment, date } = getState();
  try {
    const data = await fetchAllocation(dimension, date);
    if (data.funds        == null) data.funds        = data.rows.length;
    if (data.look_through == null) data.look_through = null;
    if (data.top_single   == null) data.top_single   = null;

    renderHeader(data);
    renderKPIs(data);
    renderPivots(dimension);
    renderViz(data, segment);
    await renderDrill(data, segment);
    renderTable(data);
    renderStubBanner(data);
  } catch (err) {
    console.error('Render failed:', err);
    const sub = document.getElementById('header-subtitle');
    if (sub && sub.textContent === 'Loading…') {
      sub.textContent = `Error: ${err.message} — check add-on log`;
    }
  }
}

// ── Admin panel ───────────────────────────────────────────────────────────────

async function renderAdmin() {
  const el = document.getElementById('admin-inner');
  try {
    const health = await fetchHealth();
    el.innerHTML = `
      <div class="admin-section">
        <h3>System status</h3>
        ${Object.entries(health).map(([k, v]) => `
          <div class="admin-row">
            <span>${k}</span>
            <span class="badge ${v === 'ok' || v === true ? 'badge-ok' : v === 'error' || v === false ? 'badge-error' : 'badge-unknown'}">${v}</span>
          </div>`).join('')}
      </div>
      <div class="admin-section">
        <h3>Manual refresh</h3>
        ${JOBS.map(j => `
          <div class="admin-row">
            <span>${j.label}</span>
            <button class="tonal-btn" onclick="triggerJob('${j.key}')">Run now</button>
          </div>`).join('')}
        <div class="job-result" id="job-result"></div>
      </div>`;
  } catch (err) {
    el.textContent = 'Could not reach API.';
  }
}

async function triggerJob(job) {
  const el = document.getElementById('job-result');
  el.textContent = 'Sending…';
  try {
    const res  = await fetch(`api/admin/refresh?job=${job}`, { method: 'POST' });
    const data = await res.json();
    el.textContent = `✓ ${data.job} accepted — check DB in ~20s`;
  } catch (err) {
    el.textContent = `✗ ${err.message}`;
  }
}

// ── Boot ──────────────────────────────────────────────────────────────────────

document.getElementById('admin-toggle').addEventListener('click', () => {
  const panel = document.getElementById('admin-panel');
  panel.hidden = !panel.hidden;
  if (!panel.hidden) renderAdmin();
});

document.getElementById('drill-close').addEventListener('click', () => {
  pushState({ segment: null });
});

window.addEventListener('hashchange', render);

// No external custom-element dependencies — render immediately.
render();
