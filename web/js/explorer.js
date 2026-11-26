// Ontology Explorer: interactive graph, filters, object and relationship detail, schema.

import { apiGet, h, table, block, details, banner, spinner, field, select, fill } from './core.js';
import { graphView, graphLegend, typeStyles } from './graph.js';
import { tableBrowser } from './data.js';

const view = { workspace: 'benchmark', level: 'model', typeInspect: null, types: null, relationships: new Set(), focus: '', depth: 1, inspect: null };
const WORKSPACES = [{ value: 'benchmark', label: 'Laptop support (benchmark)' }, { value: 'troubleshooting', label: 'Troubleshooting (diagram-based)' }];

export function renderExplorer(root, ctx, loaded) {
  // #explorer?ontology=troubleshooting opens straight on that ontology.
  const wanted = new URLSearchParams(location.hash.split('?')[1] || '').get('ontology');
  if (wanted && wanted !== view.linked && WORKSPACES.some((w) => w.value === wanted)) { view.linked = wanted; view.workspace = wanted; }
  const level = new URLSearchParams(location.hash.split('?')[1] || '').get('level');
  if (level && level !== view.linkedLevel && ['model', 'instances', 'tables'].includes(level)) { view.linkedLevel = level; view.level = level; }
  // The benchmark ontology is already loaded; the troubleshooting one is fetched each time, because cases are added to it.
  const data = view.workspace === 'benchmark' ? { schema: ctx.schema, nodes: ctx.nodes } : loaded;
  if (!data) {
    root.append(spinner('Loading ontology…'));
    Promise.all([apiGet('/ontology/schema', { workspace: view.workspace }), apiGet('/ontology/nodes', { workspace: view.workspace })])
      .then(([schema, nodes]) => { fill(root); renderExplorer(root, ctx, { schema, nodes: nodes.nodes }); })
      .catch((err) => fill(root, banner('bad', err.message)));
    return;
  }
  const switchTo = (workspace) => { Object.assign(view, { workspace, types: null, typeInspect: null, relationships: new Set(), focus: '', inspect: null }); fill(root); renderExplorer(root, ctx); };
  const setLevel = (level) => { view.level = level; fill(root); renderExplorer(root, ctx, view.workspace === 'benchmark' ? undefined : data); };
  const graphTypes = data.schema.object_types.filter((t) => t.storage === 'graph').map((t) => t.name);
  const relationships = data.schema.relationship_types_including_disabled.filter((r) => r.storage === 'graph');
  const styles = typeStyles(new Set(graphTypes));
  if (!view.types) {
    const preferred = graphTypes.filter((t) => ['Symptom', 'DiagnosticChecklist', 'Checklist', 'CheckDefinition', 'Solution'].includes(t));
    view.types = new Set(preferred.length ? preferred : graphTypes.slice(0, 2));
  }
  if (!view.inspect) view.inspect = (data.nodes.find((n) => view.types.has(n.node_type)) || data.nodes[0])?.node_id;
  const nameOf = new Map(data.nodes.map((n) => [n.node_id, n]));

  const graphBox = h('div', {}), inspector = h('div', { class: 'card' }), objectsBox = h('div', { class: 'card' });

  // One table per selected object type: a row per object, a column per declared property.
  async function drawObjects() {
    const chosen = graphTypes.filter((t) => view.types.has(t));
    if (!chosen.length) { fill(objectsBox, h('h2', {}, 'Objects as tables'), h('p', { class: 'muted' }, 'Select an object type above.')); return; }
    fill(objectsBox, h('h2', {}, 'Objects as tables'), spinner('Loading objects…'));
    try {
      const lists = await Promise.all(chosen.map((t) => apiGet('/ontology/nodes', { object_type: t, workspace: view.workspace, details: true })));
      const show = (value) => {
        if (value === null || value === undefined) return '';
        const flat = (x) => (x && typeof x === 'object' ? Object.entries(x).map(([k, v]) => `${k}: ${v}`).join(', ') : String(x));
        if (Array.isArray(value)) return h('div', {}, value.map((x) => h('div', {}, flat(x))));
        return flat(value);
      };
      fill(objectsBox, h('h2', {}, 'Objects as tables'),
        h('p', { class: 'small muted' }, 'Every object of the selected types, one row each, with a column for each property the model declares. Click a row to inspect it.'),
        chosen.map((typeName, i) => {
          const declared = data.schema.object_types.find((t) => t.name === typeName).properties.map((p) => p.name);
          const rows = lists[i].nodes;
          return h('div', { style: { marginBottom: '16px' } },
            h('h3', {}, `${typeName} (${rows.length})`),
            table([{ label: 'id', render: (n) => h('code', {}, n.node_id) }, { key: 'canonical_name', label: 'name' }, { key: 'definition', label: 'definition' },
              { label: 'synonyms', render: (n) => n.synonyms.join(', ') },
              ...declared.map((name) => ({ label: name, render: (n) => show(n.properties[name]) }))],
            rows, { maxHeight: 380, selected: (n) => n.node_id === view.inspect,
              onRow: (n) => { view.inspect = n.node_id; drawInspector(); drawObjects(); } }));
        }));
    } catch (err) { fill(objectsBox, h('h2', {}, 'Objects as tables'), banner('bad', err.message)); }
  }
  const chip = (label, pressed, onclick) => h('button', { class: 'chip', 'aria-pressed': String(pressed), onclick }, label);

  const controls = h('div', {});
  const LEVELS = [['model', 'Model', 'Object types and relationships'], ['instances', 'Instances', 'Individual objects, as a graph and as tables'], ['tables', 'Database tables', 'Raw stored rows']];
  const levelPicker = (...extra) => h('div', { class: 'explorer-bar' },
    field('Ontology', select(WORKSPACES, view.workspace, switchTo)),
    h('div', { class: 'field' }, h('span', {}, 'Level'), h('div', { class: 'segmented', role: 'tablist' }, LEVELS.map(([value, label, hint]) =>
      h('button', { role: 'tab', title: hint, 'aria-selected': String(view.level === value), onclick: () => setLevel(value) }, label)))),
    extra);

  // A compact dropdown of checkboxes. It stays open while you tick, and its button shows what is selected.
  function menu(label, items, isOn, toggle, summarise, onChange) {
    const state = h('span', { class: 'menu-state' }, summarise());
    const node = h('details', { class: 'menu' }, h('summary', {}, h('span', {}, label), state),
      h('div', { class: 'menu-panel' }, items.map((item) => h('label', {},
        h('input', { type: 'checkbox', checked: isOn(item.value), onchange: (e) => { toggle(item.value, e.target.checked); state.textContent = summarise(); onChange(); } }),
        item.label))));
    document.addEventListener('pointerdown', (e) => { if (node.open && !node.contains(e.target)) node.open = false; });
    return h('div', { class: 'field' }, h('span', {}, '\u00a0'), node);
  }

  if (view.level === 'model') { renderModel(); return; }
  if (view.level === 'tables') {
    root.append(h('h1', {}, 'Ontology Explorer'),
      h('p', { class: 'muted' }, 'The stored rows exactly as the lab reads them. Objects are in kg_node, relationships in kg_edge, and the model in the ontology_ tables. All data is synthetic.'),
      levelPicker(), tableBrowser(view.workspace));
    return;
  }

  // ---- the model level: one box per object type, one arrow per relationship type ----
  function renderModel() {
    const types = data.schema.object_types;
    const rels = data.schema.relationship_types_including_disabled;
    const nodes = types.map((t) => ({ node_id: t.name, node_type: t.name, canonical_name: t.name }));
    const edges = rels.flatMap((r) => r.source_types.flatMap((from) => r.target_types.map((to) => ({
      edge_id: `${r.storage === 'operational' ? 'stored in operational tables' : `${r.edge_count} edges`}${r.disabled ? ' · hidden' : ''}`,
      source_node_id: from, target_node_id: to, relationship_type: r.name, properties: {} }))));
    const allStyles = typeStyles(new Set(types.map((t) => t.name)));
    if (!types.some((t) => t.name === view.typeInspect)) view.typeInspect = types[0]?.name;
    const type = types.find((t) => t.name === view.typeInspect);
    const outgoing = rels.filter((r) => r.source_types.includes(type.name));
    const incoming = rels.filter((r) => r.target_types.includes(type.name));
    const relRow = (r, other, arrow) => h('div', {}, arrow, ' ', h('b', {}, r.name), ' ', other.join(', '), h('span', { class: 'muted small' }, `  ${r.description}`));
    const detail = h('div', { class: 'card' },
      h('h2', {}, type.name),
      h('p', { class: 'muted' }, type.storage === 'graph' ? `${type.instance_count} instances in the graph` : `stored in the operational table ${type.table}`),
      h('p', {}, type.description),
      h('h3', {}, 'Properties'),
      type.properties.length ? table([{ label: 'Property', render: (x) => h('code', {}, x.name) }, { key: 'type', label: 'Type' },
        { label: 'Required', render: (x) => (x.required ? 'yes' : '') }, { key: 'description', label: 'Meaning' }], type.properties, { maxHeight: 260 })
        : h('p', { class: 'muted' }, 'None declared.'),
      h('h3', { style: { marginTop: '12px' } }, 'Relationships'),
      outgoing.map((r) => relRow(r, r.target_types, '→')), incoming.map((r) => relRow(r, r.source_types, '←')),
      !outgoing.length && !incoming.length ? h('p', { class: 'muted' }, 'None.') : null,
      type.storage === 'graph' ? h('div', { class: 'controls', style: { marginTop: '12px', marginBottom: 0 } },
        h('button', { class: 'btn', onclick: () => { view.types = new Set([type.name]); view.focus = ''; view.inspect = null; setLevel('instances'); } }, `Show the ${type.instance_count} instances`)) : null);
    root.append(h('h1', {}, 'Ontology Explorer'),
      h('p', { class: 'muted' }, 'The model: what kinds of things exist and how they can relate. Click a type to see its properties; switch Level to see individual objects.'),
      levelPicker(),
      h('div', { class: 'split' },
        h('div', { class: 'card' },
          h('div', { class: 'small muted' }, `${types.length} object types, ${rels.length} relationship types · drag to rearrange, scroll to zoom, click a type`),
          graphView(nodes, edges, { layout: 'layered', boxed: true, height: 560, styles: allStyles, selected: view.typeInspect,
            onSelect: (id) => { view.typeInspect = id; setLevel('model'); } })),
        detail));
  }

  const drawControls = () => fill(controls, levelPicker(
    menu('Object types', graphTypes.map((t) => ({ value: t, label: t })), (t) => view.types.has(t),
      (t, on) => { if (on) view.types.add(t); else view.types.delete(t); view.focus = ''; },
      () => `${view.types.size} of ${graphTypes.length}`, () => { drawGraph(); drawObjects(); }),
    menu('Relationships', relationships.map((r) => ({ value: r.name, label: r.name + (r.disabled ? ' (hidden)' : '') })), (r) => view.relationships.has(r),
      (r, on) => { if (on) view.relationships.add(r); else view.relationships.delete(r); },
      () => (view.relationships.size ? `${view.relationships.size} of ${relationships.length}` : 'all'), () => drawGraph()),
    field('Focus on one object', h('input', { type: 'search', list: 'node-list', value: view.focus, placeholder: 'name or id (optional)',
      style: { width: '170px' }, onchange: (e) => { const id = resolve(e.target.value); view.focus = id || ''; if (id) view.inspect = id; drawGraph(); drawInspector(); } })),
    field('Hops', select(['1', '2', '3', '4'], String(view.depth), (v) => { view.depth = Number(v); drawGraph(); })),
    h('datalist', { id: 'node-list' }, data.nodes.map((n) => h('option', { value: `${n.canonical_name} (${n.node_id})` })))));

  const resolve = (text) => {
    const match = /\(([^()]+)\)\s*$/.exec(text);
    if (match && nameOf.has(match[1])) return match[1];
    if (nameOf.has(text.trim())) return text.trim();
    const lower = text.trim().toLowerCase();
    return data.nodes.find((n) => n.canonical_name.toLowerCase() === lower)?.node_id || null;
  };

  async function drawGraph() {
    fill(graphBox, spinner('Loading graph…'));
    try {
      const graph = await apiGet('/ontology/graph', {
        object_types: view.focus ? null : [...view.types].join(','), relationship_types: [...view.relationships].join(','),
        focus: view.focus || null, depth: view.depth, workspace: view.workspace });
      const shown = graph.nodes.slice(0, 160);
      fill(graphBox, 
        h('div', { class: 'small muted' }, `${graph.nodes.length} objects, ${graph.edges.length} relationships`
          + (graph.nodes.length > 160 ? ' · showing the first 160 objects' : '') + ' · drag to move, scroll to zoom, click an object to inspect it'),
        graphLegend(new Set(shown.map((n) => n.node_type)), styles),
        graphView(shown, graph.edges, { selected: view.inspect, styles, onSelect: (id) => { view.inspect = id; drawInspector(); drawGraph(); } }),
        details(`Relationship details (${graph.edges.length})`, () => table([
          { key: 'edge_id', label: 'Edge' }, { key: 'source_node_id', label: 'From' }, { key: 'relationship_type', label: 'Relationship' },
          { key: 'target_node_id', label: 'To' }, { label: 'Properties', render: (e) => (Object.keys(e.properties).length ? JSON.stringify(e.properties) : '') },
          { key: 'confidence', label: 'Confidence', num: true }, { key: 'source', label: 'Source' }, { key: 'ontology_version', label: 'Version' },
        ], graph.edges, { maxHeight: 300 })));
    } catch (err) {
      fill(graphBox, banner('bad', err.message));
    }
  }

  async function drawInspector() {
    if (!view.inspect) return;
    fill(inspector, spinner('Loading object…'));
    try {
      const found = await apiGet(`/ontology/node/${encodeURIComponent(view.inspect)}`, { workspace: view.workspace });
      const { node, neighbors, hierarchy } = found;
      const link = (n) => h('a', { href: '#explorer', onclick: (e) => { e.preventDefault(); view.inspect = n.node_id; view.focus = n.node_id; drawControls(); drawGraph(); drawInspector(); } },
        `${n.canonical_name} (${n.node_id})`);
      const list = (items) => (items.length ? items.flatMap((n, i) => (i ? [', ', link(n)] : [link(n)])) : 'none');
      fill(inspector, 
        h('h2', {}, node.canonical_name),
        h('p', { class: 'muted' }, h('code', {}, node.node_id), ` · ${node.node_type}`),
        h('p', {}, node.definition || 'No definition.'),
        node.synonyms.length ? h('p', {}, h('span', { class: 'muted' }, 'Also called: '), node.synonyms.join(', ')) : null,
        h('h3', {}, 'Properties'),
        Object.keys(node.properties).length ? h('dl', { class: 'kv' }, Object.entries(node.properties).flatMap(([k, v]) => {
          const flat = (x) => (x && typeof x === 'object' ? Object.entries(x).map(([kk, vv]) => `${kk}: ${vv}`).join(', ') : String(x));
          return [h('dt', {}, k), h('dd', {}, Array.isArray(v) ? v.map((x) => h('div', {}, flat(x))) : flat(v))];
        })) : h('p', { class: 'muted' }, 'None.'),
        h('p', { class: 'small muted', style: { marginTop: '8px' } }, `Source: ${node.source} · ontology version ${node.ontology_version}`),
        (hierarchy.ancestors.length || hierarchy.descendants.length) ? h('div', {},
          h('h3', {}, 'Hierarchy'),
          h('p', {}, h('span', { class: 'muted' }, 'Broader: '), list(hierarchy.ancestors)),
          h('p', {}, h('span', { class: 'muted' }, 'Narrower: '), list(hierarchy.descendants))) : null,
        h('h3', {}, `Relationships (${neighbors.length})`),
        table([
          { label: '', render: (n) => (n.direction === 'out' ? '→' : '←') }, { key: 'relationship_type', label: 'Relationship' },
          { label: 'Object', render: (n) => link(n) }, { key: 'node_type', label: 'Type' },
          { label: 'Edge properties', render: (n) => (Object.keys(n.edge_properties).length ? JSON.stringify(n.edge_properties) : '') },
        ], neighbors, { maxHeight: 260 }),
        view.workspace !== 'benchmark' ? null : details('Flattened profile (what agents D and E retrieve for this object)', () => {
          const box = h('div', {}, spinner('Loading profile…'));
          apiGet(`/graphrag/profile/${encodeURIComponent(node.node_id)}`).then((p) => fill(box, 
            h('p', { class: 'small muted' }, `Built from ${p.source_node_ids.length} objects and ${p.source_edge_ids.length} relationships.`), block(p.text, 360)))
            .catch((err) => fill(box, banner('info', err.message)));
          return box;
        }));
    } catch (err) {
      fill(inspector, banner('bad', err.message));
    }
  }

  const reference = h('div', { class: 'card' },
    h('h2', {}, 'Definitions'),
    details('Ontology schema: object types', () => table([
      { key: 'name', label: 'Object type' }, { key: 'storage', label: 'Stored in' }, { key: 'instance_count', label: 'Instances', num: true },
      { label: 'Properties', render: (t) => t.properties.map((p) => `${p.name}: ${p.type}`).join(', ') }, { key: 'description', label: 'Description' },
    ], data.schema.object_types)),
    details('Ontology schema: relationship types and constraints', () => table([
      { key: 'name', label: 'Relationship' }, { label: 'From', render: (r) => r.source_types.join(', ') }, { label: 'To', render: (r) => r.target_types.join(', ') },
      { label: 'Hierarchy', render: (r) => (r.hierarchical ? 'yes' : '') }, { key: 'storage', label: 'Stored in' }, { key: 'edge_count', label: 'Edges', num: true },
      { label: 'Hidden', render: (r) => (r.disabled ? 'hidden' : '') }, { key: 'description', label: 'Description' },
    ], data.schema.relationship_types_including_disabled)),
    view.workspace !== 'benchmark' ? null : details('Semantic view: metrics, formulas and denominators', () => {
      const box = h('div', {}, spinner('Loading…'));
      apiGet('/semantic/definition').then((d) => fill(box, 
        table([{ key: 'name', label: 'Metric' }, { key: 'logical_table', label: 'Table' }, { key: 'formula', label: 'Formula' },
          { key: 'definition', label: 'Definition' }, { key: 'numerator', label: 'Numerator' }, { key: 'denominator', label: 'Denominator' }], d.metrics),
        h('div', { style: { height: '8px' } }),
        table([{ key: 'name', label: 'Logical table' }, { key: 'grain', label: 'One row per' }, { label: 'Dimensions', render: (t) => t.dimensions.map((x) => x.name).join(', ') },
          { label: 'Facts', render: (t) => t.facts.map((x) => x.name).join(', ') }, { label: 'Joins', render: (t) => t.joins.join(' · ') }], d.logical_tables)));
      return box;
    }),
    view.workspace !== 'benchmark' ? null : details('Terminology mappings (given to agent E)', () => {
      const box = h('div', {}, spinner('Loading…'));
      apiGet('/terminology').then((t) => fill(box, 
        h('p', { class: 'small muted' }, `Version ${t.version} · ${t.path}`),
        table([{ key: 'term', label: 'Phrase' }, { key: 'kind', label: 'Kind' }, { label: 'Variants', render: (m) => m.variants.join(', ') },
          { label: 'Maps to', render: (m) => m.targets_resolved.map((x) => `${x.canonical_name} (${x.node_id})`).join(', ') },
          { label: 'Note', render: (m) => m.note || m.clarification }], t.mappings)));
      return box;
    }));

  root.append(h('h1', {}, 'Ontology Explorer'),
    h('p', { class: 'muted' }, 'The knowledge graph as stored in the KG_NODE and KG_EDGE tables, with its schema, the semantic view and the terminology mappings.'),
    controls, h('div', { class: 'split' }, h('div', { class: 'card' }, graphBox), inspector), objectsBox, reference);
  drawControls();
  drawGraph();
  drawInspector();
  drawObjects();
}
