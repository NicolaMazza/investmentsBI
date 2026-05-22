// Squarified treemap renderer — InvestmentsBI M4
// Algorithm: Bruls, Huizing & van Wijk (2000) squarified treemap.
// ~80 lines of logic; the rest is SVG rendering.

const TM_COLORS = [
  '#378ADD', '#2FA39A', '#7F77DD', '#D4A93D',
  '#C97B5C', '#C374A8', '#8A8A87', '#66A86C',
  '#E07B8A', '#5BA05B', '#C09040', '#6090C0',
];

// ── Layout ────────────────────────────────────────────────────────────────

/**
 * Compute squarified treemap rectangles.
 * @param {Array<{label, value, weight}>} items  sorted descending by value
 * @param {number} x  @param {number} y
 * @param {number} w  @param {number} h
 * @returns {Array<{label, value, weight, x, y, w, h, colorIdx}>}
 */
function squarifyLayout(items, x, y, w, h) {
  const total = items.reduce((s, i) => s + i.value, 0);
  const area  = w * h;
  const normed = items.map(i => ({ ...i, area: (i.value / total) * area }));
  const result = [];
  _layoutRecurse(normed, x, y, w, h, result);
  return result;
}

function _layoutRecurse(items, x, y, w, h, out) {
  if (!items.length) return;
  if (items.length === 1) {
    out.push({ ...items[0], x, y, w, h, colorIdx: _colorIdx(items[0].label) });
    return;
  }

  // Find the row split that minimises worst aspect ratio
  let bestWorst = Infinity;
  let bestSplit = 1;
  for (let n = 1; n <= items.length; n++) {
    const worst = _worstAspect(items.slice(0, n), w, h);
    if (worst <= bestWorst) { bestWorst = worst; bestSplit = n; }
    else break; // squarified property: stop as soon as ratio degrades
  }

  const row  = items.slice(0, bestSplit);
  const rest = items.slice(bestSplit);
  const rowArea = row.reduce((s, i) => s + i.area, 0);

  if (w >= h) {
    // Vertical strip on the left
    const sw = rowArea / h;
    let iy = y;
    for (const item of row) {
      const ih = item.area / sw;
      out.push({ ...item, x, y: iy, w: sw, h: ih, colorIdx: _colorIdx(item.label) });
      iy += ih;
    }
    _layoutRecurse(rest, x + sw, y, w - sw, h, out);
  } else {
    // Horizontal strip on the top
    const sh = rowArea / w;
    let ix = x;
    for (const item of row) {
      const iw = item.area / sh;
      out.push({ ...item, x: ix, y, w: iw, h: sh, colorIdx: _colorIdx(item.label) });
      ix += iw;
    }
    _layoutRecurse(rest, x, y + sh, w, h - sh, out);
  }
}

function _worstAspect(items, w, h) {
  const sum = items.reduce((s, i) => s + i.area, 0);
  let worst = 0;
  if (w >= h) {
    const sw = sum / h;
    for (const item of items) {
      const ih = item.area / sw;
      worst = Math.max(worst, Math.max(sw / ih, ih / sw));
    }
  } else {
    const sh = sum / w;
    for (const item of items) {
      const iw = item.area / sh;
      worst = Math.max(worst, Math.max(sh / iw, iw / sh));
    }
  }
  return worst;
}

// Stable color assignment keyed by label
const _colorCache = {};
let _colorCounter = 0;
function _colorIdx(label) {
  if (_colorCache[label] === undefined) {
    _colorCache[label] = _colorCounter++ % TM_COLORS.length;
  }
  return _colorCache[label];
}

// ── SVG renderer ──────────────────────────────────────────────────────────

/**
 * Render a squarified treemap into an SVG element.
 * @param {SVGElement}  svg
 * @param {Array<{label, value, weight}>} items
 * @param {string|null} selectedLabel  — highlighted segment (or null)
 * @param {function}    onSelect(item) — called when a rect is clicked
 */
function renderTreemap(svg, items, selectedLabel, onSelect) {
  const vb   = svg.viewBox.baseVal;
  const vw   = vb.width  || 560;
  const vh   = vb.height || 280;
  const GAP  = 2;

  svg.innerHTML = '';
  if (!items || !items.length) return;

  const sorted = [...items].sort((a, b) => b.value - a.value);
  const layout = squarifyLayout(sorted, 0, 0, vw, vh);

  for (const rect of layout) {
    const rw = Math.max(0, rect.w - GAP);
    const rh = Math.max(0, rect.h - GAP);
    const rx = rect.x + GAP / 2;
    const ry = rect.y + GAP / 2;
    const color = TM_COLORS[rect.colorIdx];
    const isSelected = rect.label === selectedLabel;

    const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    g.style.cursor = 'pointer';

    // Rectangle
    const r = _svgEl('rect', {
      x: rx, y: ry, width: rw, height: rh,
      fill: color,
      rx: 4,
      opacity: selectedLabel && !isSelected ? 0.45 : 1,
      stroke: isSelected ? '#fff' : 'none',
      'stroke-width': isSelected ? 2 : 0,
    });
    g.appendChild(r);

    // Label — only if the rect is large enough
    if (rw > 44 && rh > 26) {
      const fontSize = Math.min(13, rw / 9);
      g.appendChild(_svgText(rect.label.split(' ')[0] + (rect.label.includes(' ') && rw < 90 ? '.' : rw >= 90 ? ' ' + rect.label.split(' ').slice(1).join(' ') : ''), rx + 7, ry + 16, fontSize, '500', 'white'));
      if (rh > 44) {
        g.appendChild(_svgText(
          (rect.weight * 100).toFixed(1) + '%',
          rx + 7, ry + 16 + fontSize + 4, 11, '400', 'rgba(255,255,255,0.88)',
        ));
      }
    }

    g.addEventListener('click', () => onSelect(rect));
    svg.appendChild(g);
  }
}

function _svgEl(tag, attrs) {
  const el = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
}

function _svgText(text, x, y, size, weight, fill) {
  const el = _svgEl('text', { x, y, 'font-size': size, 'font-weight': weight, fill,
    'font-family': 'system-ui, -apple-system, sans-serif' });
  el.textContent = text;
  return el;
}
