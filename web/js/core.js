// Shared helpers: API calls, DOM building, experiment state, formatting.

export class ApiError extends Error {}

async function handle(response) {
  let data = null;
  try { data = await response.json(); } catch { /* not JSON */ }
  if (!response.ok) {
    const detail = data && data.detail !== undefined ? data.detail : `Server error ${response.status}`;
    throw new ApiError(typeof detail === 'string' ? detail : (detail.message || JSON.stringify(detail)));
  }
  return data;
}

export async function apiGet(path, params = {}) {
  const query = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== null && v !== undefined && v !== ''));
  try {
    return await handle(await fetch(path + (query.size ? `?${query}` : '')));
  } catch (err) {
    if (err instanceof ApiError) throw err;
    throw new ApiError('Cannot reach the backend.');
  }
}

export async function apiPost(path, body = {}) {
  try {
    return await handle(await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }));
  } catch (err) {
    if (err instanceof ApiError) throw err;
    throw new ApiError('Cannot reach the backend.');
  }
}

// h('div', {class: 'card', onclick: fn}, child, 'text', [more]) -> element. Text is always set as text, never HTML.
export function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2), value);
    else if (key === 'class') node.className = value;
    else if (key === 'style' && typeof value === 'object') Object.assign(node.style, value);
    else if (key in node && !['list', 'form'].includes(key) && typeof value !== 'string') node[key] = value;
    else node.setAttribute(key, value === true ? '' : value);
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

// Replace a node's children, ignoring null / undefined / false entries.
export function fill(node, ...children) {
  node.replaceChildren(...children.flat(Infinity).filter((child) => child !== null && child !== undefined && child !== false));
  return node;
}

export const SVG_NS = 'http://www.w3.org/2000/svg';
export function s(tag, attrs = {}, ...children) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export const pct = (v, digits = 0) => (v === null || v === undefined ? 'n/a' : `${(v * 100).toFixed(digits)}%`);
export const num = (v, digits = 1) => (v === null || v === undefined ? 'n/a' : Number(v).toLocaleString(undefined, { maximumFractionDigits: digits }));
export const json = (value) => JSON.stringify(value, null, 1);

export function field(label, control) {
  return h('label', { class: 'field' }, h('span', {}, label), control);
}

export function select(options, value, onchange, attrs = {}) {
  const node = h('select', { ...attrs, onchange: (e) => onchange(e.target.value) },
    options.map((o) => (typeof o === 'string' ? h('option', { value: o }, o) : h('option', { value: o.value }, o.label))));
  node.value = value;
  return node;
}

// columns: [{key, label, num?, render?(row)}]
export function table(columns, rows, { onRow, maxHeight, selected } = {}) {
  const body = h('tbody', {}, rows.map((row, index) => {
    const tr = h('tr', { class: [onRow ? 'clickable' : '', selected && selected(row) ? 'selected' : ''].join(' ').trim() || null,
      onclick: onRow ? () => onRow(row, index) : null },
    columns.map((c) => {
      const value = c.render ? c.render(row) : row[c.key];
      return h('td', { class: c.num ? 'num' : null }, value === null || value === undefined ? '' : value);
    }));
    return tr;
  }));
  const wrap = h('div', { class: 'table-wrap' },
    h('table', {}, h('thead', {}, h('tr', {}, columns.map((c) => h('th', { class: c.num ? 'num' : null }, c.label)))), body));
  if (maxHeight) wrap.style.setProperty('--max-h', `${maxHeight}px`);
  if (!rows.length) wrap.append(h('div', { class: 'muted', style: { padding: '10px' } }, 'Nothing to show.'));
  return wrap;
}

export function block(value, maxHeight) {
  const node = h('pre', { class: 'block' }, typeof value === 'string' ? value : json(value));
  if (maxHeight) node.style.setProperty('--max-h', `${maxHeight}px`);
  return node;
}

export function details(summary, content, open = false) {
  const node = h('details', { open }, h('summary', {}, summary));
  let built = false;
  const build = () => {
    if (built) return;
    built = true;
    node.append(typeof content === 'function' ? content() : content);
  };
  if (open) build(); else node.addEventListener('toggle', build);
  return node;
}

export function banner(kind, text) {
  return h('div', { class: `banner ${kind}` }, text);
}

export function spinner(text) {
  return h('div', { class: 'muted' }, h('span', { class: 'spinner' }), text);
}

// Text with **bold** spans, as model answers often use them.
export function richText(text) {
  const node = h('div', { class: 'answer' });
  String(text || '').split(/(\*\*[^*\n]+\*\*)/).forEach((part) => {
    if (part.startsWith('**') && part.endsWith('**') && part.length > 4) node.append(h('strong', {}, part.slice(2, -2)));
    else node.append(part);
  });
  return node;
}

const tip = () => document.getElementById('tooltip');
export function showTip(event, lines) {
  const node = tip();
  node.replaceChildren(...lines.map((line, i) => h('div', {}, i === 0 ? h('b', {}, line) : line)));
  node.hidden = false;
  const x = Math.min(event.clientX + 12, window.innerWidth - node.offsetWidth - 8);
  const y = Math.min(event.clientY + 12, window.innerHeight - node.offsetHeight - 8);
  node.style.left = `${x}px`;
  node.style.top = `${y}px`;
}
export function hideTip() { tip().hidden = true; }
export function withTip(node, lines) {
  node.setAttribute('tabindex', '0');
  node.addEventListener('pointermove', (e) => showTip(e, lines()));
  node.addEventListener('pointerleave', hideTip);
  node.addEventListener('focus', () => { const r = node.getBoundingClientRect(); showTip({ clientX: r.right, clientY: r.top }, lines()); });
  node.addEventListener('blur', hideTip);
  return node;
}

// ------------------------------------------------------------ experiment state

const KEY = 'ontolab.experiment.v1';
const defaults = { disabledTools: [], overrides: {}, llmProvider: '', llmModel: '', maxToolCalls: null };
export const experiment = { ...defaults };
try { Object.assign(experiment, JSON.parse(localStorage.getItem(KEY) || '{}')); } catch { /* storage unavailable */ }
const listeners = new Set();
export function setExperiment(patch) {
  Object.assign(experiment, patch);
  try { localStorage.setItem(KEY, JSON.stringify(experiment)); } catch { /* ignore */ }
  listeners.forEach((fn) => fn());
}
export function onExperimentChange(fn) { listeners.add(fn); }

// Request fields shared by the playground and evaluation runs.
export function experimentBody() {
  const body = { disabled_tools: experiment.disabledTools, description_overrides: experiment.overrides };
  if (experiment.maxToolCalls !== null && experiment.maxToolCalls !== '') body.max_tool_calls = Number(experiment.maxToolCalls);
  if (experiment.llmProvider) {
    body.llm_provider = experiment.llmProvider;
    if (experiment.llmModel) body.llm_model = experiment.llmModel;
  }
  return body;
}

export const CONFIGS = ['A', 'B', 'C', 'D', 'E'];
export const configColor = (key) => `var(--series-${CONFIGS.indexOf(key) + 1})`;
