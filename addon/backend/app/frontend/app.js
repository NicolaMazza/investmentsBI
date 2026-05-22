// ── Constants ────────────────────────────────────────────────────────────────

const JOBS = [
  { key: 'ishares_holdings',  label: 'iShares holdings' },
  { key: 'position_snapshot', label: 'Position snapshot (ECB FX + Ghostfolio)' },
];

// M3-era colour palette — works well on dark backgrounds
const PALETTE = [
  '#4FC3F7', // sky blue
  '#81C784', // sage green
  '#FF8A65', // coral
  '#B39DDB', // lavender
  '#F48FB1', // rose
  '#4DD0E1', // teal
  '#A5D6A7', // mint
  '#FFD54F', // gold
  '#FFCC80', // peach
  '#90CAF9', // periwinkle
  '#EF9A9A', // blush
  '#B0BEC5', // silver
];

// ── State ─────────────────────────────────────────────────────────────────────

let allocChart = null;
let activeDimension = 'sector';

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatEUR(value) {
  return new Intl.NumberFormat('en-EU', {
    style: 'currency', currency: 'EUR', maximumFractionDigits: 0,
  }).format(value);
}

function formatPct(weight) {
  return (weight * 100).toFixed(1) + '%';
}

// ── Center-text plugin for Chart.js donut ─────────────────────────────────────

const centerTextPlugin = {
  id: 'centerText',
  afterDraw(chart) {
    if (!chart.data._total) return;
    const { ctx, chartArea: { left, top, width, height } } = chart;
    const cx = left + width / 2;
    const cy = top + height / 2;
    ctx.save();
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.font = '600 1.4rem system-ui, sans-serif';
    ctx.fillStyle = '#e2e8f0';
    ctx.fillText(formatEUR(chart.data._total), cx, cy - 10);
    ctx.font = '0.72rem system-ui, sans-serif';
    ctx.fillStyle = '#94a3b8';
    ctx.fillText('Portfolio value', cx, cy + 14);
    ctx.restore();
  },
};
Chart.register(centerTextPlugin);

// ── Allocation chart ─────────────────────────────────────────────────────────

async function loadAllocation(dimension = 'sector') {
  const loading = document.getElementById('chart-loading');
  loading.hidden = false;

  try {
    const res = await fetch(`api/allocation?dimension=${dimension}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderAllocation(data);
  } catch (err) {
    loading.hidden = true;
    console.error('Allocation fetch failed:', err);
  }
}

function renderAllocation(data) {
  document.getElementById('chart-loading').hidden = true;
  document.getElementById('as-of-date').textContent = data.as_of_date ?? '—';

  const banner = document.getElementById('stub-banner');
  banner.hidden = !data.stub;

  const labels  = data.rows.map(r => r.label);
  const values  = data.rows.map(r => r.value_eur);
  const colors  = data.rows.map((_, i) => PALETTE[i % PALETTE.length]);

  // ── Chart ──────────────────────────────────────────────────────────────────
  const ctx = document.getElementById('alloc-chart').getContext('2d');

  if (allocChart) allocChart.destroy();

  allocChart = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels,
      datasets: [{
        data: values,
        backgroundColor: colors,
        borderColor: '#0f172a',
        borderWidth: 2,
        hoverBorderWidth: 0,
      }],
      _total: data.total_eur,
    },
    options: {
      cutout: '65%',
      animation: { duration: 600, easing: 'easeInOutQuart' },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const row = data.rows[ctx.dataIndex];
              return ` ${formatPct(row.weight)}  ${formatEUR(row.value_eur)}`;
            },
          },
          backgroundColor: '#1e293b',
          borderColor: '#334155',
          borderWidth: 1,
          titleColor: '#e2e8f0',
          bodyColor: '#94a3b8',
          padding: 12,
        },
      },
    },
  });

  // ── Legend table ───────────────────────────────────────────────────────────
  const legend = document.getElementById('alloc-legend');
  legend.innerHTML = `
    <thead>
      <tr>
        <th></th>
        <th class="col-label">Sector</th>
        <th class="col-num">Weight</th>
        <th class="col-num">Value (EUR)</th>
      </tr>
    </thead>
    <tbody>
      ${data.rows.map((row, i) => `
        <tr>
          <td><span class="dot" style="background:${colors[i]}"></span></td>
          <td class="col-label">${row.label}</td>
          <td class="col-num">${formatPct(row.weight)}</td>
          <td class="col-num">${formatEUR(row.value_eur)}</td>
        </tr>`).join('')}
    </tbody>`;
}

// ── Admin view ───────────────────────────────────────────────────────────────

async function loadAdmin() {
  const el = document.getElementById('admin-content');
  try {
    const res = await fetch('api/health');
    const data = await res.json();
    el.innerHTML = `
      <div class="status-card">
        <h2>System status</h2>
        ${Object.entries(data).map(([k, v]) => `
          <div class="status-row">
            <span>${k}</span>
            <span class="badge ${v === 'ok' || v === true ? 'badge-ok' : v === 'error' || v === false ? 'badge-error' : 'badge-unknown'}">
              ${v}
            </span>
          </div>`).join('')}
      </div>
      <div class="status-card" style="margin-top:1rem">
        <h2>Manual refresh</h2>
        ${JOBS.map(j => `
          <div class="status-row">
            <span>${j.label}</span>
            <button class="refresh-btn" onclick="triggerJob('${j.key}')">Run now</button>
          </div>`).join('')}
        <div id="job-result" style="margin-top:0.75rem;font-size:0.85rem;color:#94a3b8"></div>
      </div>`;
  } catch (err) {
    el.innerHTML = `<p style="color:#f87171">Could not reach API: ${err.message}</p>`;
  }
}

async function triggerJob(job) {
  const el = document.getElementById('job-result');
  el.textContent = 'Sending…';
  el.style.color = '#94a3b8';
  try {
    const res = await fetch(`api/admin/refresh?job=${job}`, { method: 'POST' });
    const data = await res.json();
    el.textContent = `✓ ${data.job} accepted — check DB in ~20s`;
    el.style.color = '#34d399';
  } catch (err) {
    el.textContent = `✗ ${err.message}`;
    el.style.color = '#f87171';
  }
}

// ── Tab switching ─────────────────────────────────────────────────────────────

function switchView(index) {
  document.getElementById('view-portfolio').hidden = index !== 0;
  document.getElementById('view-admin').hidden     = index !== 1;
  if (index === 1) loadAdmin();
}

// ── Dimension chips ───────────────────────────────────────────────────────────

function initDimensionChips() {
  const chips = document.querySelectorAll('#dimension-chips md-filter-chip:not([disabled])');
  chips.forEach(chip => {
    chip.addEventListener('click', () => {
      const dim = chip.dataset.dim;
      if (dim === activeDimension) return;
      // deselect all, select clicked
      chips.forEach(c => c.selected = false);
      chip.selected = true;
      activeDimension = dim;
      loadAllocation(dim);
    });
  });
}

// ── Boot ──────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  // Tab change
  const tabs = document.getElementById('main-tabs');
  tabs.addEventListener('change', () => switchView(tabs.activeTabIndex));

  // Dimension chips (wait for custom elements to be ready)
  customElements.whenDefined('md-filter-chip').then(initDimensionChips);

  // Initial load
  loadAllocation(activeDimension);
});
