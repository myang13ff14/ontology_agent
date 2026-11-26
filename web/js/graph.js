// Interactive ontology graph: force layout, pan, zoom, drag, click to inspect.
// Object types are told apart by colour AND shape (there are more types than
// distinguishable hues), and every node carries its name.

import { h, s, withTip } from './core.js';

const SHAPES = ['circle', 'square', 'diamond'];
const HUES = 5;

export function typeStyles(types) {
  const sorted = [...types].sort();
  return Object.fromEntries(sorted.map((t, i) => [t, { color: `var(--series-${(i % HUES) + 1})`, shape: SHAPES[Math.floor(i / HUES) % SHAPES.length] }]));
}

function shape(kind, size, color) {
  if (kind === 'square') return s('rect', { class: 'shape', x: -size, y: -size, width: size * 2, height: size * 2, rx: 2.5, fill: color });
  if (kind === 'diamond') return s('path', { class: 'shape', d: `M0,${-size * 1.25} L${size * 1.25},0 L0,${size * 1.25} L${-size * 1.25},0 Z`, fill: color });
  return s('circle', { class: 'shape', r: size, fill: color });
}

export function graphLegend(types, styles) {
  return h('div', { class: 'legend' }, [...types].sort().map((t) => {
    const icon = s('svg', { viewBox: '-8 -8 16 16' }, shape(styles[t].shape, 5, styles[t].color));
    return h('span', {}, icon, t);
  }));
}

function layout(nodes, edges, width, height) {
  const n = nodes.length;
  const pos = nodes.map((_, i) => {
    const a = (i / Math.max(n, 1)) * Math.PI * 2;
    const r = Math.min(width, height) * 0.36;
    return { x: width / 2 + r * Math.cos(a), y: height / 2 + r * Math.sin(a), vx: 0, vy: 0 };
  });
  const index = new Map(nodes.map((node, i) => [node.node_id, i]));
  const links = edges.map((e) => [index.get(e.source_node_id), index.get(e.target_node_id)]).filter(([a, b]) => a !== undefined && b !== undefined);
  const k = Math.sqrt((width * height) / Math.max(n, 1)) * 0.75;
  const steps = n > 120 ? 160 : 260;
  for (let step = 0; step < steps; step += 1) {
    const cool = 1 - step / steps;
    for (let i = 0; i < n; i += 1) {
      for (let j = i + 1; j < n; j += 1) {
        let dx = pos[i].x - pos[j].x, dy = pos[i].y - pos[j].y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 0.01) { dx = (i - j) * 0.1 + 0.1; dy = 0.1; d2 = dx * dx + dy * dy; }
        if (d2 > 16 * k * k) continue; // repulsion is short-range, so separate clusters do not push each other away
        const f = (k * k) / d2;
        pos[i].vx += dx * f; pos[i].vy += dy * f; pos[j].vx -= dx * f; pos[j].vy -= dy * f;
      }
    }
    for (const [a, b] of links) {
      const dx = pos[a].x - pos[b].x, dy = pos[a].y - pos[b].y;
      const d = Math.sqrt(dx * dx + dy * dy) || 0.01;
      const f = (d - k * 0.9) / d * 0.5;
      pos[a].vx -= dx * f; pos[a].vy -= dy * f; pos[b].vx += dx * f; pos[b].vy += dy * f;
    }
    for (const p of pos) {
      p.vx += (width / 2 - p.x) * 0.06; p.vy += (height / 2 - p.y) * 0.06;
      const speed = Math.sqrt(p.vx * p.vx + p.vy * p.vy) || 1;
      const limit = Math.max(2, 40 * cool);
      p.x += (p.vx / speed) * Math.min(speed, limit); p.y += (p.vy / speed) * Math.min(speed, limit);
      p.vx = 0; p.vy = 0;
    }
  }
  // Fit the result into the frame, whatever the simulation produced.
  const pad = { l: 24, r: 130, t: 20, b: 20 };
  const xs = pos.map((p) => p.x), ys = pos.map((p) => p.y);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  const scale = Math.min((width - pad.l - pad.r) / Math.max(maxX - minX, 1), (height - pad.t - pad.b) / Math.max(maxY - minY, 1), 1.6);
  const offX = pad.l + ((width - pad.l - pad.r) - (maxX - minX) * scale) / 2;
  const offY = pad.t + ((height - pad.t - pad.b) - (maxY - minY) * scale) / 2;
  for (const p of pos) { p.x = offX + (p.x - minX) * scale; p.y = offY + (p.y - minY) * scale; }
  return pos;
}

// nodes: [{node_id, node_type, canonical_name}], edges: [{edge_id, source_node_id, target_node_id, relationship_type, properties}]
export function graphView(nodes, edges, { selected = null, onSelect = null, height = 460, styles = null } = {}) {
  const container = h('div', { class: 'graph' });
  container.style.setProperty('--graph-h', `${height}px`);
  if (!nodes.length) {
    container.append(h('div', { class: 'muted', style: { padding: '16px' } }, 'No objects match these filters.'));
    return container;
  }
  const W = 700, H = Math.round((700 * height) / 640);
  styles = styles || typeStyles(new Set(nodes.map((nd) => nd.node_type)));
  const pos = layout(nodes, edges, W, H);
  const at = new Map(nodes.map((node, i) => [node.node_id, pos[i]]));
  const view = { x: 0, y: 0, w: W, h: H };
  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': 'Ontology graph' },
    s('defs', {}, s('marker', { id: 'arrow', viewBox: '0 0 10 10', refX: 19, refY: 5, markerWidth: 6, markerHeight: 6, orient: 'auto-start-reverse' },
      s('path', { d: 'M0,1 L9,5 L0,9 Z', fill: 'var(--axis)' }))));
  const applyView = () => svg.setAttribute('viewBox', `${view.x} ${view.y} ${view.w} ${view.h}`);
  const showLabels = edges.length <= 60;
  const edgeLayer = s('g'), nodeLayer = s('g');
  const lines = [];
  const visible = edges.filter((e) => at.has(e.source_node_id) && at.has(e.target_node_id));
  for (const e of visible) {
    const line = s('line', { class: 'edge', 'marker-end': 'url(#arrow)' });
    const hit = withTip(s('line', { stroke: 'transparent', 'stroke-width': 10 }),
      () => [e.relationship_type, `${e.source_node_id} → ${e.target_node_id}`, e.edge_id,
        ...(e.properties && Object.keys(e.properties).length ? [JSON.stringify(e.properties)] : [])]);
    const label = showLabels ? s('text', { class: 'edge-label', 'text-anchor': 'middle' }, e.relationship_type) : null;
    edgeLayer.append(line, hit);
    if (label) edgeLayer.append(label);
    lines.push({ e, line, hit, label });
  }
  const place = () => {
    for (const { e, line, hit, label } of lines) {
      const a = at.get(e.source_node_id), b = at.get(e.target_node_id);
      for (const el of [line, hit]) { el.setAttribute('x1', a.x); el.setAttribute('y1', a.y); el.setAttribute('x2', b.x); el.setAttribute('y2', b.y); }
      if (label) { label.setAttribute('x', (a.x + b.x) / 2); label.setAttribute('y', (a.y + b.y) / 2 - 2); }
    }
  };
  const toLocal = (event) => {
    const rect = svg.getBoundingClientRect();
    return { x: view.x + ((event.clientX - rect.left) / rect.width) * view.w, y: view.y + ((event.clientY - rect.top) / rect.height) * view.h };
  };
  nodes.forEach((node) => {
    const p = at.get(node.node_id);
    const st = styles[node.node_type] || { color: 'var(--secondary)', shape: 'circle' };
    const name = node.canonical_name.length > 26 ? `${node.canonical_name.slice(0, 25)}…` : node.canonical_name;
    const g = s('g', { class: `node${node.node_id === selected ? ' selected' : ''}`, transform: `translate(${p.x},${p.y})`, tabindex: 0 },
      shape(st.shape, 6.5, st.color), s('text', { x: 10, y: 3.5 }, name));
    withTip(g, () => [node.canonical_name, `${node.node_type} · ${node.node_id}`]);
    let moved = false;
    g.addEventListener('pointerdown', (event) => {
      event.stopPropagation();
      g.setPointerCapture(event.pointerId);
      moved = false;
      const move = (ev) => {
        moved = true;
        const q = toLocal(ev);
        p.x = q.x; p.y = q.y;
        g.setAttribute('transform', `translate(${p.x},${p.y})`);
        place();
      };
      const up = () => { g.removeEventListener('pointermove', move); g.removeEventListener('pointerup', up); if (!moved && onSelect) onSelect(node.node_id); };
      g.addEventListener('pointermove', move);
      g.addEventListener('pointerup', up);
    });
    g.addEventListener('keydown', (event) => { if (event.key === 'Enter' && onSelect) onSelect(node.node_id); });
    nodeLayer.append(g);
  });
  svg.append(edgeLayer, nodeLayer);
  place();

  svg.addEventListener('pointerdown', (event) => {
    const start = { cx: event.clientX, cy: event.clientY, x: view.x, y: view.y };
    const rect = svg.getBoundingClientRect();
    svg.setPointerCapture(event.pointerId);
    const move = (ev) => {
      view.x = start.x - ((ev.clientX - start.cx) / rect.width) * view.w;
      view.y = start.y - ((ev.clientY - start.cy) / rect.height) * view.h;
      applyView();
    };
    const up = () => { svg.removeEventListener('pointermove', move); svg.removeEventListener('pointerup', up); };
    svg.addEventListener('pointermove', move);
    svg.addEventListener('pointerup', up);
  });
  svg.addEventListener('wheel', (event) => {
    event.preventDefault();
    const factor = event.deltaY > 0 ? 1.12 : 0.89;
    const q = toLocal(event);
    const w = Math.max(120, Math.min(W * 4, view.w * factor));
    const ratio = w / view.w;
    view.x = q.x - (q.x - view.x) * ratio; view.y = q.y - (q.y - view.y) * ratio; view.w = w; view.h = view.h * ratio;
    applyView();
  }, { passive: false });

  container.append(svg);
  return container;
}
