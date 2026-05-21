async function loadHealth() {
  const el = document.getElementById('status');
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
        <div class="status-row">
          <span>iShares holdings</span>
          <button class="refresh-btn" onclick="triggerJob('ishares_holdings')">Run now</button>
        </div>
        <div id="job-result" style="margin-top:0.75rem;font-size:0.85rem;color:#94a3b8"></div>
      </div>`;
  } catch (err) {
    el.innerHTML = `<p style="color:#f87171">Could not reach API: ${err.message}</p>`;
  }
}

async function triggerJob(job) {
  const el = document.getElementById('job-result');
  el.textContent = 'Sending…';
  try {
    const res = await fetch(`api/admin/refresh?job=${job}`, { method: 'POST' });
    const data = await res.json();
    el.textContent = `✓ ${data.status} — check Adminer in ~20s`;
    el.style.color = '#34d399';
  } catch (err) {
    el.textContent = `✗ ${err.message}`;
    el.style.color = '#f87171';
  }
}

loadHealth();
