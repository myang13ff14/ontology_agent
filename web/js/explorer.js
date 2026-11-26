// Ontology Explorer: interactive graph, filters, object and relationship detail, schema.

import { apiGet, h, table, block, details, banner, spinner, field, select, fill } from './core.js';
import { graphView, graphLegend, typeStyles } from './graph.js';

const view = { types: null, relationships: new Set(), focus: '', depth: 1, inspect: null };

export function renderExplorer(root, ctx) {
  const graphTypes = ctx.schema.object_types.filter((t) => t.storage === 'graph').map((t) => t.name);
  const relationships = ctx.schema.relationship_types_including_disabled.filter((r) => r.storage === 'graph');
  const styles = typeStyles(new Set(graphTypes));
  if (!view.types) view.types = new Set(graphTypes.filter((t) => ['Symptom', 'DiagnosticChecklist'].includes(t)).length
    ? ['Symptom', 'DiagnosticChecklist'] : graphTypes.slice(0, 2));
  if (!view.inspect) view.inspect = ctx.nodes[0]?.node_id;
  const nameOf = new Map(ctx.nodes.map((n) => [n.node_id, n]));

  const graphBox = h('div', {}), inspector = h('div', { class: 'card' });
  const chip = (label, pressed, onclick) => h('button', { class: 'chip', 'aria-pressed': String(pressed), onclick }, label);

  const controls = h('div', { class: 'card' });
  const drawControls = () => fill(controls, 
    h('h3', {}, 'Object types'),
    h('div', { class: 'chips' }, graphTypes.map((t) => chip(t, view.types.has(t), () => { view.types.has(t) ? view.types.delete(t) : view.types.add(t); view.focus = ''; drawControls(); drawGraph(); }))),
    h('h3', { style: { marginTop: '10px' } }, 'Relationship types (none selected = all)'),
    h('div', { class: 'chips' }, relationships.map((r) => chip(r.name + (r.disabled ? ' (hidden)' : ''), view.relationships.has(r.name),
      () => { view.relationships.has(r.name) ? view.relationships.delete(r.name) : view.relationships.add(r.name); drawControls(); drawGraph(); }))),
    h('div', { class: 'controls', style: { marginTop: '10px', marginBottom: 0 } },
      field('Focus on one object and its neighbourhood', h('input', { type: 'search', list: 'node-list', value: view.focus, placeholder: 'type a name or id; empty shows the filtered graph',
        style: { width: '340px' }, onchange: (e) => { const id = resolve(e.target.value); view.focus = id || ''; if (id) view.inspect = id; drawGraph(); drawInspector(); } })),
      field('Hops', select(['1', '2', '3', '4'], String(view.depth), (v) => { view.depth = Number(v); drawGraph(); })),
      h('datalist', { id: 'node-list' }, ctx.nodes.map((n) => h('option', { value: `${n.canonical_name} (${n.node_id})` })))));

  const resolve = (text) => {
    const match = /\(([^()]+)\)\s*$/.exec(text);
    if (match && nameOf.has(match[1])) return match[1];
    if (nameOf.has(text.trim())) return text.trim();
    const lower = text.trim().toLowerCase();
    return ctx.nodes.find((n) => n.canonical_name.toLowerCase() === lower)?.node_id || null;
  };

  async function drawGraph() {
    fill(graphBox, spinner('Loading graph…'));
    try {
      const graph = await apiGet('/ontology/graph', {
        object_types: view.focus ? null : [...view.types].join(','), relationship_types: [...view.relationships].join(','),
        focus: view.focus || null, depth: view.depth });
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
      const data = await apiGet(`/ontology/node/${encodeURIComponent(view.inspect)}`);
      const { node, neighbors, hierarchy } = data;
      const link = (n) => h('a', { href: '#explorer', onclick: (e) => { e.preventDefault(); view.inspect = n.node_id; view.focus = n.node_id; drawControls(); drawGraph(); drawInspector(); } },
        `${n.canonical_name} (${n.node_id})`);
      const list = (items) => (items.length ? items.flatMap((n, i) => (i ? [', ', link(n)] : [link(n)])) : 'none');
      fill(inspector, 
        h('h2', {}, node.canonical_name),
        h('p', { class: 'muted' }, h('code', {}, node.node_id), ` · ${node.node_type}`),
        h('p', {}, node.definition || 'No definition.'),
        node.synonyms.length ? h('p', {}, h('span', { class: 'muted' }, 'Also called: '), node.synonyms.join(', ')) : null,
        h('h3', {}, 'Properties'),
        Object.keys(node.properties).length ? h('dl', { class: 'kv' }, Object.entries(node.properties).flatMap(([k, v]) => [h('dt', {}, k), h('dd', {}, String(v))])) : h('p', { class: 'muted' }, 'None.'),
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
        details('Flattened profile (what agents D and E retrieve for this object)', () => {
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
    ], ctx.schema.object_types)),
    details('Ontology schema: relationship types and constraints', () => table([
      { key: 'name', label: 'Relationship' }, { label: 'From', render: (r) => r.source_types.join(', ') }, { label: 'To', render: (r) => r.target_types.join(', ') },
      { label: 'Hierarchy', render: (r) => (r.hierarchical ? 'yes' : '') }, { key: 'storage', label: 'Stored in' }, { key: 'edge_count', label: 'Edges', num: true },
      { label: 'Hidden', render: (r) => (r.disabled ? 'hidden' : '') }, { key: 'description', label: 'Description' },
    ], ctx.schema.relationship_types_including_disabled)),
    details('Semantic view: metrics, formulas and denominators', () => {
      const box = h('div', {}, spinner('Loading…'));
      apiGet('/semantic/definition').then((d) => fill(box, 
        table([{ key: 'name', label: 'Metric' }, { key: 'logical_table', label: 'Table' }, { key: 'formula', label: 'Formula' },
          { key: 'definition', label: 'Definition' }, { key: 'numerator', label: 'Numerator' }, { key: 'denominator', label: 'Denominator' }], d.metrics),
        h('div', { style: { height: '8px' } }),
        table([{ key: 'name', label: 'Logical table' }, { key: 'grain', label: 'One row per' }, { label: 'Dimensions', render: (t) => t.dimensions.map((x) => x.name).join(', ') },
          { label: 'Facts', render: (t) => t.facts.map((x) => x.name).join(', ') }, { label: 'Joins', render: (t) => t.joins.join(' · ') }], d.logical_tables)));
      return box;
    }),
    details('Terminology mappings (given to agent E)', () => {
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
    controls, h('div', { class: 'split' }, h('div', { class: 'card' }, graphBox), inspector), reference);
  drawControls();
  drawGraph();
  drawInspector();
}
