// Small SVG charts. Every chart has a hover/focus tooltip on each mark, and the
// same numbers are always available in a table next to it.

import { h, s, withTip, pct } from './core.js';

const fmt = (value, percent) => (percent ? pct(value) : (Math.round(value * 100) / 100).toLocaleString());

function ticks(max, percent) {
  if (percent) return [0, 0.25, 0.5, 0.75, 1];
  const rough = max / 4 || 1;
  const pow = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 5, 10].map((m) => m * pow).find((v) => v >= rough);
  const out = [];
  for (let v = 0; v <= max + step * 0.01; v += step) out.push(Math.round(v * 1e6) / 1e6);
  return out;
}

function frame(title, svg, extra) {
  return h('div', { class: 'chart' }, h('h3', {}, title), svg, extra);
}

// items: [{label, value, color}]   one series: the title names it, so no legend.
export function barChart(title, items, { percent = false, height = 210 } = {}) {
  const W = 420, H = height, m = { l: 42, r: 10, t: 16, b: 24 };
  const max = percent ? 1 : Math.max(...items.map((i) => i.value || 0), 1e-9);
  const tk = ticks(max, percent);
  const top = tk[tk.length - 1];
  const y = (v) => m.t + (H - m.t - m.b) * (1 - v / top);
  const band = (W - m.l - m.r) / Math.max(items.length, 1);
  const bw = Math.min(24, band - 2);
  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': title });
  tk.forEach((t) => svg.append(
    s('line', { class: t === 0 ? 'baseline' : 'gridline', x1: m.l, x2: W - m.r, y1: y(t), y2: y(t) }),
    s('text', { class: 'axis-text', x: m.l - 6, y: y(t) + 3.5, 'text-anchor': 'end' }, fmt(t, percent))));
  items.forEach((item, i) => {
    const cx = m.l + band * (i + 0.5);
    const value = item.value ?? 0;
    const top0 = y(value), base = y(0), r = Math.min(4, (base - top0) / 2);
    const path = `M${cx - bw / 2},${base} V${top0 + r} Q${cx - bw / 2},${top0} ${cx - bw / 2 + r},${top0} H${cx + bw / 2 - r} Q${cx + bw / 2},${top0} ${cx + bw / 2},${top0 + r} V${base} Z`;
    svg.append(withTip(s('path', { class: 'mark', d: path, fill: item.color }), () => [fmt(value, percent), item.label]),
      s('text', { class: 'value-text', x: cx, y: top0 - 4, 'text-anchor': 'middle' }, item.value === null || item.value === undefined ? 'n/a' : fmt(value, percent)),
      s('text', { class: 'axis-text', x: cx, y: H - 7, 'text-anchor': 'middle' }, item.label));
  });
  return frame(title, svg);
}

// points: [{label, x, y, color}]   each point is labelled directly, so identity never rests on colour.
export function scatterChart(title, points, { xLabel, yLabel, xPercent = false, yPercent = true, height = 230 } = {}) {
  const W = 420, H = height, m = { l: 44, r: 16, t: 28, b: 36 };
  const usable = points.filter((p) => p.x !== null && p.x !== undefined && p.y !== null && p.y !== undefined);
  const xt = ticks(xPercent ? 1 : Math.max(...usable.map((p) => p.x), 1e-9), xPercent);
  const yt = ticks(yPercent ? 1 : Math.max(...usable.map((p) => p.y), 1e-9), yPercent);
  const X = (v) => m.l + (W - m.l - m.r) * (v / xt[xt.length - 1]);
  const Y = (v) => m.t + (H - m.t - m.b) * (1 - v / yt[yt.length - 1]);
  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': title });
  yt.forEach((t) => svg.append(
    s('line', { class: t === 0 ? 'baseline' : 'gridline', x1: m.l, x2: W - m.r, y1: Y(t), y2: Y(t) }),
    s('text', { class: 'axis-text', x: m.l - 6, y: Y(t) + 3.5, 'text-anchor': 'end' }, fmt(t, yPercent))));
  xt.forEach((t) => svg.append(s('text', { class: 'axis-text', x: X(t), y: H - m.b + 14, 'text-anchor': 'middle' }, fmt(t, xPercent))));
  svg.append(s('text', { class: 'axis-text', x: (m.l + W - m.r) / 2, y: H - 4, 'text-anchor': 'middle' }, xLabel),
    s('text', { class: 'axis-text', x: 4, y: 11 }, yLabel));
  // Nudge labels of overlapping points apart horizontally so each stays readable.
  const seen = new Map();
  usable.forEach((p) => {
    const key = `${Math.round(X(p.x) / 14)}:${Math.round(Y(p.y) / 14)}`;
    const n = seen.get(key) || 0;
    seen.set(key, n + 1);
    const cx = X(p.x), cy = Y(p.y);
    svg.append(s('circle', { class: 'dot', cx, cy, r: 5.5, fill: p.color }),
      s('text', { class: 'point-label', x: cx + 9 + n * 11, y: cy + 4 }, p.label),
      withTip(s('circle', { cx, cy, r: 14, fill: 'transparent', class: 'mark' }),
        () => [`Agent ${p.label}`, `${yLabel}: ${fmt(p.y, yPercent)}`, `${xLabel}: ${fmt(p.x, xPercent)}`]));
  });
  const missing = points.filter((p) => !usable.includes(p)).map((p) => p.label);
  return frame(title, svg, missing.length ? h('div', { class: 'small muted' }, `Not shown (no value): ${missing.join(', ')}`) : null);
}

// rows: [{label, parts: [{name, value}]}], series: [{name, color}]   horizontal stacked bars with a legend.
export function stackedBars(title, rows, series, { height } = {}) {
  const W = 420, rowH = 26, m = { l: 34, r: 34, t: 6, b: 6 };
  const H = height || m.t + m.b + rows.length * rowH;
  const max = Math.max(...rows.map((r) => r.parts.reduce((a, p) => a + p.value, 0)), 1);
  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': title });
  rows.forEach((row, i) => {
    const yy = m.t + i * rowH + (rowH - 16) / 2;
    let x = m.l;
    svg.append(s('text', { class: 'axis-text', x: m.l - 8, y: yy + 12, 'text-anchor': 'end' }, row.label));
    const total = row.parts.reduce((a, p) => a + p.value, 0);
    row.parts.filter((p) => p.value > 0).forEach((part) => {
      const w = (W - m.l - m.r) * (part.value / max);
      const color = series.find((sr) => sr.name === part.name).color;
      svg.append(withTip(s('rect', { class: 'mark', x, y: yy, width: Math.max(w - 2, 1), height: 16, rx: 3, fill: color }),
        () => [String(part.value), `${part.name} · agent ${row.label}`]));
      x += w;
    });
    svg.append(s('text', { class: 'value-text', x: x + 4, y: yy + 12 }, String(total)));
  });
  const legend = h('div', { class: 'legend' }, series.map((sr) =>
    h('span', {}, h('span', { style: { width: '10px', height: '10px', borderRadius: '2px', background: sr.color, display: 'inline-block' } }), sr.name)));
  return frame(title, svg, legend);
}

// Heat table: one hue, light to dark; every cell shows its value, so colour only reinforces it.
export function heatTable(title, rowLabels, colLabels, valueAt, { percent = true, rowHeader = '' } = {}) {
  const cell = (r, c) => {
    const value = valueAt(r, c);
    if (value === null || value === undefined) return h('td', { class: 'cell muted' }, '·');
    const t = Math.max(0, Math.min(1, value));
    const td = h('td', { class: 'cell' }, fmt(value, percent));
    td.style.background = `color-mix(in oklab, var(--seq-high) ${Math.round(t * 100)}%, var(--seq-low))`;
    td.style.color = (document.documentElement.matches(':root') && window.matchMedia('(prefers-color-scheme: dark)').matches)
      ? (t > 0.55 ? '#0b0b0b' : '#ffffff') : (t > 0.45 ? '#ffffff' : '#0b0b0b');
    return withTip(td, () => [fmt(value, percent), `${r} · agent ${c}`]);
  };
  return h('div', { class: 'chart heat' }, h('h3', {}, title), h('div', { class: 'table-wrap' },
    h('table', {}, h('thead', {}, h('tr', {}, h('th', {}, rowHeader), colLabels.map((c) => h('th', { class: 'num', style: { textAlign: 'center' } }, c)))),
      h('tbody', {}, rowLabels.map((r) => h('tr', {}, h('td', {}, r), colLabels.map((c) => cell(r, c))))))));
}
