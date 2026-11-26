// Raw stored rows, table by table. Used as the "Tables" level of the Ontology Explorer.

import { apiGet, h, table, block, banner, spinner, field, fill } from './core.js';

const PAGE = 50;
const state = {}; // per database: { table, offset, search, picked }

// Cells that hold JSON are shown compactly in the grid and pretty-printed in the row detail.
function parse(value) {
  if (typeof value !== 'string' || !/^[[{]/.test(value)) return null;
  try { return JSON.parse(value); } catch { return null; }
}
function cell(value) {
  if (value === null || value === undefined) return h('span', { class: 'muted' }, '–');
  const text = String(value);
  return text.length > 90 ? `${text.slice(0, 88)}…` : text;
}

export function tableBrowser(workspace) {
  const view = (state[workspace] ||= { table: null, offset: 0, search: '', picked: null });
  const body = h('div', {}, spinner('Loading tables…'));
  const draw = () => {
    apiGet('/data/tables', { workspace }).then(({ tables, database }) => {
      if (!tables.some((t) => t.name === view.table)) view.table = (tables.find((t) => t.name === 'kg_node') || tables[0])?.name;
      const current = tables.find((t) => t.name === view.table);
      const rowsBox = h('div', {}, spinner('Loading rows…'));
      const list = table([{ label: 'Table', render: (t) => h('code', {}, t.name) }, { key: 'rows', label: 'Rows', num: true }, { key: 'note', label: 'What it holds' }],
        tables, { selected: (t) => t.name === view.table, onRow: (t) => { Object.assign(view, { table: t.name, offset: 0, search: '', picked: null }); draw(); } });
      const search = h('input', { type: 'search', value: view.search, placeholder: 'find text in any column…', style: { width: '260px' },
        onchange: (e) => { view.search = e.target.value.trim(); view.offset = 0; view.picked = null; draw(); } });
      fill(body,
        h('div', { class: 'card' }, h('h2', {}, 'Tables'), h('p', { class: 'small muted' }, `Database file: ${database}`), list),
        h('div', { class: 'card' },
          h('h2', {}, h('code', {}, current.name)), h('p', { class: 'muted' }, current.note),
          h('div', { class: 'controls' }, field('Filter', search),
            h('span', { class: 'small muted' }, current.columns.map((c) => `${c.name}: ${c.type.toLowerCase()}`).join(' · '))),
          rowsBox));

      apiGet(`/data/table/${current.name}`, { workspace, limit: PAGE, offset: view.offset, search: view.search || null }).then((page) => {
        const first = page.total ? page.offset + 1 : 0, last = page.offset + page.rows.length;
        const detail = h('div', {});
        const show = (row) => {
          view.picked = JSON.stringify(row);
          fill(detail, h('h3', { style: { marginTop: '12px' } }, 'Selected row'),
            h('dl', { class: 'kv' }, page.columns.flatMap((c) => {
              const parsed = parse(row[c.name]);
              return [h('dt', {}, c.name), h('dd', {}, parsed ? block(parsed, 220) : (row[c.name] === null ? h('span', { class: 'muted' }, 'null') : String(row[c.name])))];
            })));
        };
        fill(rowsBox,
          table(page.columns.map((c) => ({ label: c.name, render: (r) => cell(r[c.name]) })), page.rows, { maxHeight: 420, onRow: show }),
          h('div', { class: 'controls', style: { marginTop: '10px', marginBottom: 0 } },
            h('button', { class: 'btn', disabled: page.offset === 0, onclick: () => { view.offset = Math.max(0, view.offset - PAGE); view.picked = null; draw(); } }, 'Previous'),
            h('button', { class: 'btn', disabled: last >= page.total, onclick: () => { view.offset += PAGE; view.picked = null; draw(); } }, 'Next'),
            h('span', { class: 'small muted' }, `Rows ${first}–${last} of ${page.total.toLocaleString()}${view.search ? ` matching “${view.search}”` : ''} · click a row to read it in full`)),
          detail);
      }).catch((err) => fill(rowsBox, banner('bad', err.message)));
    }).catch((err) => fill(body, banner('bad', err.message)));
  };
  draw();
  return body;
}
