// Tool Registry: tools per configuration, schemas, capabilities, enable/disable, edit description, manual run.

import { apiPost, h, table, block, banner, select, field, experiment, setExperiment, json, fill } from './core.js';

const view = { config: 'all', tool: null, result: null, args: {} };

export function renderRegistry(root, ctx) {
  const visible = ctx.tools.filter((t) => view.config === 'all' || t.configs.includes(view.config));
  if (!visible.some((t) => t.name === view.tool)) view.tool = visible[0]?.name;
  const tool = ctx.tools.find((t) => t.name === view.tool);
  const rerender = () => { fill(root, ); renderRegistry(root, ctx); };
  const toggle = (name, enabled) => setExperiment({
    disabledTools: enabled ? experiment.disabledTools.filter((t) => t !== name) : [...experiment.disabledTools, name] });

  const list = table([
    { label: 'On', render: (t) => h('input', { type: 'checkbox', checked: !experiment.disabledTools.includes(t.name), 'aria-label': `Enable ${t.name}`,
      onclick: (e) => e.stopPropagation(), onchange: (e) => toggle(t.name, e.target.checked) }) },
    { label: 'Tool', render: (t) => h('code', {}, t.name) }, { key: 'category', label: 'Category' },
    { label: 'Agents', render: (t) => t.configs.join(' ') || '–' },
    { label: 'Operations', render: (t) => t.capabilities.operations.join(', ') },
    { label: 'Access', render: (t) => t.capabilities.access },
  ], visible, { onRow: (t) => { view.tool = t.name; view.result = null; rerender(); }, selected: (t) => t.name === view.tool, maxHeight: 330 });

  root.append(h('h1', {}, 'Tool Registry'),
    h('p', { class: 'muted' }, 'Every function an agent can call. Disabling a tool, or rewording its description, changes what agents can do and read without touching the ontology or the tool’s code.'),
    h('div', { class: 'controls' }, field('Show tools available to', select(
      [{ value: 'all', label: 'all tools' }, ...ctx.agents.map((a) => ({ value: a.key, label: `${a.key} · ${a.name}` }))], view.config,
      (v) => { view.config = v; rerender(); }))),
    h('div', { class: 'card' }, list));
  if (!tool) return;

  const current = experiment.overrides[tool.name] ?? tool.default_description;
  const description = h('textarea', { rows: 9 }, current);
  const template = Object.fromEntries((tool.input_schema.required || []).map((name) => [name, '']));
  const args = h('textarea', { rows: 6, class: 'mono' }, view.args[tool.name] ?? json(template));
  const resultBox = h('div', {});
  const showResult = (r) => fill(resultBox, 
    h('p', {}, h('span', { class: `badge ${r.ok ? 'good' : 'bad'}` }, r.ok ? 'ok' : r.error.code), ` ${r.duration_ms} ms · ontology ${r.ontology_version}`),
    r.generated_sql ? block(r.generated_sql, 160) : null, block(r.ok ? r.result : r.error, 380));
  if (view.result) showResult(view.result);

  root.append(h('div', { class: 'grid two' },
    h('div', { class: 'card' },
      h('h2', {}, h('code', {}, tool.name)),
      h('h3', {}, 'Description shown to the model'),
      description,
      h('div', { class: 'controls', style: { marginTop: '8px' } },
        h('button', { class: 'btn', onclick: () => {
          const text = description.value.trim();
          const overrides = { ...experiment.overrides };
          if (text && text !== tool.default_description) overrides[tool.name] = text; else delete overrides[tool.name];
          setExperiment({ overrides });
        } }, 'Use this description in experiments'),
        experiment.overrides[tool.name] ? h('span', { class: 'badge warn' }, 'edited') : h('span', { class: 'muted small' }, 'default')),
      h('h3', {}, 'Ontology capabilities exposed'),
      h('dl', { class: 'kv' },
        h('dt', {}, 'Finds from text'), h('dd', {}, tool.capabilities.entry_objects.join(', ') || '–'),
        h('dt', {}, 'Needs an id of'), h('dd', {}, tool.capabilities.input_objects.join(', ') || '–'),
        h('dt', {}, 'Returns'), h('dd', {}, tool.capabilities.output_objects.join(', ') || '–'),
        h('dt', {}, 'Relationships'), h('dd', {}, tool.capabilities.relationships.join(', ') || '–'),
        h('dt', {}, 'Operations'), h('dd', {}, tool.capabilities.operations.join(', ') || '–'),
        h('dt', {}, 'Access'), h('dd', {}, tool.capabilities.access)),
      h('p', { class: 'small muted', style: { marginTop: '6px' } }, '*graph = every object or relationship type stored in the graph; *operational = operational record types.')),
    h('div', { class: 'card' },
      h('h3', {}, 'Input schema'), block(tool.input_schema, 200),
      h('h3', { style: { marginTop: '10px' } }, 'Output schema'), block(tool.output_schema, 120),
      h('h3', { style: { marginTop: '10px' } }, 'Run it manually'),
      args,
      h('div', { class: 'controls', style: { marginTop: '8px' } }, h('button', { class: 'btn primary', onclick: async (e) => {
        view.args[tool.name] = args.value;
        let parsed;
        try { parsed = JSON.parse(args.value || '{}'); } catch (err) { fill(resultBox, banner('bad', `Arguments are not valid JSON: ${err.message}`)); return; }
        e.target.disabled = true;
        try { view.result = await apiPost(`/tools/${tool.name}/execute`, { arguments: parsed }); showResult(view.result); }
        catch (err) { fill(resultBox, banner('bad', err.message)); }
        e.target.disabled = false;
      } }, 'Execute')),
      resultBox)));
}
