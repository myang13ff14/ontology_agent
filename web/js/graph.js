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

// Top-down layout for small diagrams: each node sits one row below the deepest node pointing at it.
function layered(nodes, edges, width, height) {
  const ids = nodes.map((n) => n.node_id);
  const out = new Map(ids.map((id) => [id, []]));
  for (const e of edges) if (e.source_node_id !== e.target_node_id && out.has(e.source_node_id) && out.has(e.target_node_id)) out.get(e.source_node_id).push(e.target_node_id);
  const depth = new Map(ids.map((id) => [id, 0]));
  const visit = (id, trail) => {
    for (const next of out.get(id)) {
      if (trail.has(next)) continue; // a cycle: ignore the edge that closes it
      if (depth.get(next) < depth.get(id) + 1) { depth.set(next, depth.get(id) + 1); visit(next, new Set([...trail, next])); }
    }
  };
  const incoming = new Set(edges.filter((e) => e.source_node_id !== e.target_node_id).map((e) => e.target_node_id));
  const roots = ids.filter((id) => !incoming.has(id));
  (roots.length ? roots : ids.slice(0, 1)).forEach((id) => visit(id, new Set([id])));
  ids.forEach((id) => { if (depth.get(id) === 0 && !roots.includes(id)) visit(id, new Set([id])); });
  const rows = [];
  ids.forEach((id) => { (rows[depth.get(id)] ||= []).push(id); });
  const filled = rows.filter(Boolean);
  const at = new Map();
  filled.forEach((row, r) => row.forEach((id, i) => at.set(id, {
    x: 90 + ((width - 180) * (i + 0.5)) / row.length, y: 40 + ((height - 80) * r) / Math.max(filled.length - 1, 1) })));
  return nodes.map((n) => at.get(n.node_id));
}

// nodes: [{node_id, node_type, canonical_name}], edges: [{edge_id, source_node_id, target_node_id, relationship_type, properties}]
export function graphView(nodes, edges, { selected = null, onSelect = null, height = 460, styles = null, layout: mode = 'force', nodeSize = 6.5, boxed = false } = {}) {
  const container = h('div', { class: 'graph' });
  container.style.setProperty('--graph-h', `${height}px`);
  if (!nodes.length) {
    container.append(h('div', { class: 'muted', style: { padding: '16px' } }, 'No objects match these filters.'));
    return container;
  }
  const W = 700, H = Math.round((700 * height) / 640);
  styles = styles || typeStyles(new Set(nodes.map((nd) => nd.node_type)));
  const pos = mode === 'layered' ? layered(nodes, edges, W, H) : layout(nodes, edges, W, H);
  const at = new Map(nodes.map((node, i) => [node.node_id, pos[i]]));
  const view = { x: 0, y: 0, w: W, h: H };
  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': 'Ontology graph' },
    s('defs', {}, s('marker', { id: 'arrow', viewBox: '0 0 10 10', refX: 19, refY: 5, markerWidth: 6, markerHeight: 6, orient: 'auto-start-reverse' },
      s('path', { d: 'M0,1 L9,5 L0,9 Z', fill: 'var(--axis)' })),
    s('marker', { id: 'arrow-tip', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: 'auto-start-reverse' },
      s('path', { d: 'M0,1 L9,5 L0,9 Z', fill: 'var(--secondary)' }))));
  const BOX = { w: 150, h: 34 }; // boxed nodes: a labelled rectangle per node, as in a model diagram
  const applyView = () => svg.setAttribute('viewBox', `${view.x} ${view.y} ${view.w} ${view.h}`);
  const showLabels = edges.length <= 60;
  const edgeLayer = s('g'), nodeLayer = s('g'), labelLayer = s('g'); // labels sit above nodes so boxes never hide them
  const lines = [];
  const visible = edges.filter((e) => at.has(e.source_node_id) && at.has(e.target_node_id));
  for (const e of visible) {
    const loop = e.source_node_id === e.target_node_id; // a relationship from a type to itself
    const line = s(loop ? 'path' : 'line', { class: 'edge', 'marker-end': boxed ? 'url(#arrow-tip)' : 'url(#arrow)' });
    const hit = withTip(s(loop ? 'path' : 'line', { stroke: 'transparent', 'stroke-width': 10, fill: 'none' }),
      () => [e.relationship_type, `${e.source_node_id} → ${e.target_node_id}`, e.edge_id,
        ...(e.properties && Object.keys(e.properties).length ? [JSON.stringify(e.properties)] : [])]);
    const label = showLabels ? s('text', { class: 'edge-label', 'text-anchor': 'middle' }, e.relationship_type) : null;
    edgeLayer.append(line, hit);
    if (label) labelLayer.append(label);
    lines.push({ e, line, hit, label, loop });
  }
  const place = () => {
    for (const { e, line, hit, label, loop } of lines) {
      const a = at.get(e.source_node_id), b = at.get(e.target_node_id);
      if (loop) {
        const ox = boxed ? BOX.w / 2 : 4;
        const d = `M${a.x - ox},${a.y - 6} C${a.x - ox - 58},${a.y - 44} ${a.x - ox - 58},${a.y + 40} ${a.x - ox - 2},${a.y + 8}`;
        for (const el of [line, hit]) el.setAttribute('d', d);
        if (label) { label.setAttribute('x', a.x - ox - 46); label.setAttribute('y', a.y - 30); }
        continue;
      }
      let x1 = a.x, y1 = a.y, x2 = b.x, y2 = b.y;
      if (boxed) { // start and end on the box borders so the arrowhead is visible
        const dx = b.x - a.x, dy = b.y - a.y;
        const t = Math.min(dx ? (BOX.w / 2) / Math.abs(dx) : Infinity, dy ? (BOX.h / 2) / Math.abs(dy) : Infinity);
        if (t < 0.5) { x1 = a.x + dx * t; y1 = a.y + dy * t; x2 = b.x - dx * t; y2 = b.y - dy * t; }
      }
      for (const el of [line, hit]) { el.setAttribute('x1', x1); el.setAttribute('y1', y1); el.setAttribute('x2', x2); el.setAttribute('y2', y2); }
      const k = boxed ? 0.36 : 0.5; // boxed diagrams: label nearer the source, where edges have not yet crossed
      if (label) { label.setAttribute('x', x1 + (x2 - x1) * k); label.setAttribute('y', y1 + (y2 - y1) * k + 3); }
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
    const g = s('g', { class: `node${boxed ? ' boxed' : ''}${node.node_id === selected ? ' selected' : ''}`, transform: `translate(${p.x},${p.y})`, tabindex: 0 },
      boxed ? [s('rect', { class: 'shape box', x: -BOX.w / 2, y: -BOX.h / 2, width: BOX.w, height: BOX.h, rx: 9, style: `--tone: ${st.color}` }),
        s('text', { x: 0, y: 4, 'text-anchor': 'middle' }, name)]
        : [shape(st.shape, nodeSize, st.color), s('text', { x: nodeSize + 4, y: 3.5 }, name)]);
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
  svg.append(edgeLayer, nodeLayer, labelLayer);
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
