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
      </div>`;
  } catch (err) {
    el.innerHTML = `<p style="color:#f87171">Could not reach API: ${err.message}</p>`;
  }
}

loadHealth();
