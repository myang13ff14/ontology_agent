// Boot, sidebar (experiment controls) and section routing.

import { apiGet, apiPost, h, select, field, experiment, setExperiment, onExperimentChange, banner, ApiError, fill } from './core.js';
import { renderTroubleshooting } from './troubleshooting.js';
import { renderExplorer } from './explorer.js';
import { renderRegistry } from './registry.js';
import { renderPlayground } from './playground.js';
import { renderCoverage } from './coverage.js';
import { renderDashboard } from './dashboard.js';

const SECTIONS = [
  ['explorer', 'Ontology Explorer', renderExplorer],
  ['registry', 'Tool Registry', renderRegistry],
  ['playground', 'Agent Playground', renderPlayground],
  ['troubleshooting', 'Troubleshooting', renderTroubleshooting],
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

// Which sidebar groups are open, and whether the sidebar is shown at all. Remembered per browser.
const UI_KEY = 'ontolab.ui.v1';
const ui = { sidebarHidden: false, open: {} };
try { Object.assign(ui, JSON.parse(localStorage.getItem(UI_KEY) || '{}')); } catch { /* storage unavailable */ }
const saveUi = () => { try { localStorage.setItem(UI_KEY, JSON.stringify(ui)); } catch { /* ignore */ } };

// A collapsible sidebar group. Closed, its summary line still says what is set.
function group(key, title, summary, tone, ...content) {
  const node = h('details', { class: 'side-group', open: Boolean(ui.open[key]) },
    h('summary', {}, h('span', { class: 'side-title' }, title), h('span', { class: `side-state ${tone || ''}` }, summary)),
    h('div', { class: 'side-body' }, content));
  node.addEventListener('toggle', () => { ui.open[key] = node.open; saveUi(); });
  return node;
}

function applySidebarVisibility() {
  document.getElementById('sidebar').hidden = ui.sidebarHidden;
  const button = document.getElementById('sidebar-toggle');
  button.setAttribute('aria-pressed', String(!ui.sidebarHidden));
  button.title = ui.sidebarHidden ? 'Show experiment controls' : 'Hide experiment controls';
}

function renderSidebar() {
  const { status } = ctx;
  const side = document.getElementById('sidebar');
  const { ontology, llm, counts, profiles, limits } = status;
  const graphRelationships = ctx.schema.relationship_types_including_disabled.filter((r) => r.storage === 'graph');
  const hidden = new Set(ontology.disabled.relationship_types);
  const overrideNames = Object.keys(experiment.overrides);
  const modelLabel = experiment.llmProvider ? `${experiment.llmProvider}${experiment.llmModel ? ` / ${experiment.llmModel}` : ''}` : `${llm.provider} / ${llm.model}`;
  const toolState = [experiment.disabledTools.length ? `${experiment.disabledTools.length} off` : '', overrideNames.length ? `${overrideNames.length} edited` : ''].filter(Boolean).join(', ');

  fill(side,
    h('div', { class: 'side-head' },
      h('h2', {}, 'Ontology Agent Lab'),
      h('p', { class: 'muted small' }, `${ontology.name} v${ontology.version} · ${counts.kg_node} objects · ${counts.service_case.toLocaleString()} cases · synthetic data`),
      llm.is_mock ? h('div', { class: 'banner warn small' }, 'Mock model: a fixed policy, not an AI model.') : null),
    h('h3', {}, 'Experiment controls'),

    group('model', 'Model', modelLabel, experiment.llmProvider ? 'changed' : '',
      field('Model for requests', select([{ value: '', label: 'server default' }, { value: 'openai', label: 'OpenAI (name a model below)' }, { value: 'mock', label: 'mock policy (no AI)' }],
        experiment.llmProvider, (v) => setExperiment({ llmProvider: v }))),
      field('Model name (optional)', h('input', { type: 'text', value: experiment.llmModel, placeholder: 'e.g. gpt-5.5',
        onchange: (e) => setExperiment({ llmModel: e.target.value.trim() }) })),
      field(`Max tool calls per question (default ${limits.max_tool_calls})`, h('input', { type: 'number', min: 0, max: 40,
        value: experiment.maxToolCalls ?? '', placeholder: String(limits.max_tool_calls),
        onchange: (e) => setExperiment({ maxToolCalls: e.target.value === '' ? null : Number(e.target.value) }) }))),

    group('tools', 'Tools', toolState || 'all on', toolState ? 'changed' : '',
      h('p', { class: 'small muted' }, 'Ticked tools are removed from every agent configuration.'),
      checklist(ctx.tools, (name) => experiment.disabledTools.includes(name),
        (name, on) => setExperiment({ disabledTools: on ? [...experiment.disabledTools, name] : experiment.disabledTools.filter((t) => t !== name) }),
        (tool) => tool.category),
      overrideNames.length ? h('p', { class: 'small muted' }, `Edited descriptions: ${overrideNames.join(', ')} `,
        h('button', { class: 'btn small', onclick: () => setExperiment({ overrides: {} }) }, 'Reset')) : null,
      experiment.disabledTools.length ? h('button', { class: 'btn', onclick: () => setExperiment({ disabledTools: [] }) }, 'Enable all') : null),

    group('ontology', 'Ontology', hidden.size ? `${hidden.size} hidden` : 'complete', hidden.size ? 'changed' : '',
      h('p', { class: 'small muted' }, 'Hide relationship types from every tool. Server-wide; tools are unchanged.'),
      checklist(graphRelationships, (name) => hidden.has(name), (name, on) => { if (on) hidden.add(name); else hidden.delete(name); }),
      h('button', { class: 'btn', onclick: async (e) => {
        e.target.disabled = true;
        try { await apiPost('/ontology/experiment/disable', { relationship_types: [...hidden] }); await ctx.reload(); }
        catch (err) { alertInline(side, err); e.target.disabled = false; }
      } }, 'Apply')),

    group('profiles', 'Profiles', profiles.stale ? 'stale' : 'up to date', profiles.stale ? 'warn' : '',
      profiles.stale ? h('div', { class: 'banner warn small' }, 'The ontology changed after the profiles were built. Agents D and E are reading old knowledge.') : null,
      h('p', { class: 'small muted' }, `${profiles.profile_count} profiles · ${profiles.profile_version || 'not built'} · ${profiles.embedder}`),
      h('button', { class: 'btn', onclick: async (e) => {
        e.target.disabled = true;
        try { await apiPost('/graphrag/rebuild'); await ctx.reload(); } catch (err) { alertInline(side, err); e.target.disabled = false; }
      } }, 'Rebuild profiles')),

    h('p', { class: 'small muted side-foot' }, `Limits: depth ${limits.max_depth}, ${limits.max_results} results, memory ${status.memory.window_turns} turns.`));
  applySidebarVisibility();
}

function alertInline(parent, err) {
  parent.prepend(banner('bad', err instanceof ApiError ? err.message : String(err)));
}

document.getElementById('sidebar-toggle').addEventListener('click', () => { ui.sidebarHidden = !ui.sidebarHidden; saveUi(); applySidebarVisibility(); });
window.addEventListener('hashchange', renderSection);
onExperimentChange(() => { renderSidebar(); renderSection(); });

load().then(() => { renderSidebar(); renderSection(); }).catch((err) => {
  fill(main, banner('bad', `${err.message} Start the backend with: uvicorn ontolab.api.main:app --port 8600`));
});
