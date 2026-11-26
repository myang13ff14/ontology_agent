// Boot, sidebar (experiment controls) and section routing.

import { apiGet, apiPost, h, select, field, experiment, setExperiment, onExperimentChange, banner, ApiError, fill } from './core.js';
import { renderExplorer } from './explorer.js';
import { renderRegistry } from './registry.js';
import { renderPlayground } from './playground.js';
import { renderCoverage } from './coverage.js';
import { renderDashboard } from './dashboard.js';

const SECTIONS = [
  ['explorer', 'Ontology Explorer', renderExplorer],
  ['registry', 'Tool Registry', renderRegistry],
  ['playground', 'Agent Playground', renderPlayground],
  ['coverage', 'Coverage Explorer', renderCoverage],
  ['dashboard', 'Evaluation Dashboard', renderDashboard],
];
const ctx = {};
const main = document.getElementById('main');

async function load() {
  const [status, tools, schema, agents, nodes, benchmark] = await Promise.all([
    apiGet('/status'), apiGet('/tools'), apiGet('/ontology/schema'), apiGet('/agents'), apiGet('/ontology/nodes'), apiGet('/benchmark')]);
  Object.assign(ctx, { status, tools: tools.tools, schema, agents: agents.configs, nodes: nodes.nodes, benchmark: benchmark.tasks });
  ctx.reload = async () => { await load(); renderSidebar(); renderSection(); };
}

function current() {
  const key = location.hash.replace('#', '').split('?')[0];
  return SECTIONS.find(([k]) => k === key) || SECTIONS[0];
}

function renderTabs() {
  const [active] = current();
  fill(document.getElementById('tabs'), ...SECTIONS.map(([key, label]) =>
    h('button', { role: 'tab', 'aria-selected': String(key === active), onclick: () => { location.hash = key; } }, label)));
  const { ontology, llm } = ctx.status;
  document.getElementById('toolbar-status').textContent = `${ontology.name} v${ontology.version} · ${llm.provider}/${llm.model}`;
}

function renderSection() {
  renderTabs();
  const [, , render] = current();
  fill(main, );
  main.scrollTop = 0;
  try {
    render(main, ctx);
  } catch (err) {
    main.append(banner('bad', `This section failed to render: ${err.message}`));
    console.error(err);
  }
}

function checklist(items, isChecked, onToggle, groupOf) {
  const box = h('div', { class: 'checklist' });
  let group = null;
  for (const item of items) {
    const g = groupOf ? groupOf(item) : null;
    if (g && g !== group) { group = g; box.append(h('div', { class: 'group' }, g)); }
    box.append(h('label', {}, h('input', { type: 'checkbox', checked: isChecked(item.name), onchange: (e) => onToggle(item.name, e.target.checked) }), item.name));
  }
  return box;
}

function renderSidebar() {
  const { status } = ctx;
  const side = document.getElementById('sidebar');
  const { ontology, llm, counts, profiles, limits } = status;
  const graphRelationships = ctx.schema.relationship_types_including_disabled.filter((r) => r.storage === 'graph');
  let hidden = new Set(ontology.disabled.relationship_types);
  const overrideNames = Object.keys(experiment.overrides);

  fill(side, 
    h('section', {},
      h('h2', {}, 'Ontology Agent Lab'),
      h('p', { class: 'muted small' }, 'All data is synthetic. A local reimplementation of the ideas in Snowflake’s ontology-grounded Cortex Agents post.'),
      h('div', { class: 'row' }, h('span', { class: 'muted' }, 'Ontology'), h('span', {}, `${ontology.name} v${ontology.version}`)),
      h('div', { class: 'row' }, h('span', { class: 'muted' }, 'Graph'), h('span', {}, `${counts.kg_node} objects · ${counts.kg_edge} links`)),
      h('div', { class: 'row' }, h('span', { class: 'muted' }, 'Operational'), h('span', {}, `${counts.service_case.toLocaleString()} cases`)),
      h('div', { class: 'row' }, h('span', { class: 'muted' }, 'Server model'), h('span', {}, `${llm.provider} / ${llm.model}`)),
      llm.is_mock ? h('div', { class: 'banner warn small', style: { marginTop: '8px' } }, 'Mock model: a fixed policy, not an AI model. Results only test the pipeline.') : null),

    h('section', {},
      h('h3', {}, 'Experiment controls'),
      field('Model for requests', select([{ value: '', label: 'server default' }, ...status.llm_providers.map((p) => ({ value: p, label: p }))],
        experiment.llmProvider, (v) => setExperiment({ llmProvider: v }))),
      h('div', { style: { height: '6px' } }),
      field('Model name (optional)', h('input', { type: 'text', value: experiment.llmModel, placeholder: 'provider default',
        onchange: (e) => setExperiment({ llmModel: e.target.value.trim() }) })),
      h('div', { style: { height: '6px' } }),
      field(`Max tool calls per question (default ${limits.max_tool_calls})`, h('input', { type: 'number', min: 0, max: 40,
        value: experiment.maxToolCalls ?? '', placeholder: String(limits.max_tool_calls),
        onchange: (e) => setExperiment({ maxToolCalls: e.target.value === '' ? null : Number(e.target.value) }) })),
      h('div', { style: { height: '8px' } }),
      h('div', { class: 'field' }, h('span', {}, `Disabled tools (${experiment.disabledTools.length})`),
        checklist(ctx.tools, (name) => experiment.disabledTools.includes(name),
          (name, on) => setExperiment({ disabledTools: on ? [...experiment.disabledTools, name] : experiment.disabledTools.filter((t) => t !== name) }),
          (tool) => tool.category)),
      overrideNames.length ? h('p', { class: 'small muted', style: { marginTop: '6px' } }, `Edited descriptions: ${overrideNames.join(', ')} `,
        h('button', { class: 'btn small', onclick: () => setExperiment({ overrides: {} }) }, 'Reset')) : null),

    h('section', {},
      h('h3', {}, 'Ontology experiment'),
      h('div', { class: 'field' }, h('span', {}, 'Hide relationship types from every tool'),
        checklist(graphRelationships, (name) => hidden.has(name), (name, on) => { if (on) hidden.add(name); else hidden.delete(name); })),
      h('div', { style: { height: '6px' } }),
      h('button', { class: 'btn', onclick: async (e) => {
        e.target.disabled = true;
        try { await apiPost('/ontology/experiment/disable', { relationship_types: [...hidden] }); await ctx.reload(); }
        catch (err) { alertInline(side, err); e.target.disabled = false; }
      } }, 'Apply to ontology'),
      h('p', { class: 'small muted', style: { marginTop: '6px' } }, 'Server-wide. Tools are unchanged; only the graph they see changes.')),

    h('section', {},
      h('h3', {}, 'Flattened profiles'),
      profiles.stale ? h('div', { class: 'banner warn small' }, 'Stale: the ontology changed after the profiles were built. Agents D and E are reading old knowledge.') : null,
      h('p', { class: 'small muted' }, `${profiles.profile_count} profiles · ${profiles.profile_version || 'not built'} · ${profiles.embedder}`),
      h('button', { class: 'btn', onclick: async (e) => {
        e.target.disabled = true;
        try { await apiPost('/graphrag/rebuild'); await ctx.reload(); } catch (err) { alertInline(side, err); e.target.disabled = false; }
      } }, 'Rebuild profiles'),
      h('p', { class: 'small muted', style: { marginTop: '8px' } }, `Traversal depth limit ${limits.max_depth}, result limit ${limits.max_results} (ontolab/config.py).`)),
  );
}

function alertInline(parent, err) {
  parent.prepend(banner('bad', err instanceof ApiError ? err.message : String(err)));
}

window.addEventListener('hashchange', renderSection);
onExperimentChange(() => { renderSidebar(); renderSection(); });

load().then(() => { renderSidebar(); renderSection(); }).catch((err) => {
  fill(main, banner('bad', `${err.message} Start the backend with: uvicorn ontolab.api.main:app --port 8600`));
});
