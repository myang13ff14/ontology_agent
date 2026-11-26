// Coverage Explorer: what the registered tools can reach, with no model involved.

import { apiPost, h, table, banner, spinner, select, field, pct, experiment, CONFIGS, configColor, fill } from './core.js';
import { scatterChart } from './charts.js';

const view = { split: 'all', config: 'B' };
const LABEL = { direct: 'direct', multi_call: 'multi', none: '–' };

export function renderCoverage(root, ctx) {
  const body = h('div', {}, spinner('Computing coverage…'));
  root.append(h('h1', {}, 'Coverage Explorer'),
    h('p', { class: 'muted' }, 'Computed from tool capability declarations and the ontology schema. No model runs, so this separates what the tools cannot do from what a model fails to do.'),
    h('div', { class: 'controls' }, field('Benchmark split', select(['all', 'test', 'dev'], view.split, (v) => { view.split = v; fill(root, ); renderCoverage(root, ctx); }))),
    body);

  Promise.all([apiPost('/coverage', { split: view.split, disabled_tools: experiment.disabledTools }), apiPost('/coverage', { split: view.split })])
    .then(([current, baseline]) => draw(current, baseline)).catch((err) => fill(body, banner('bad', err.message)));

  function draw(current, baseline) {
    const rows = CONFIGS.map((key) => ({ key, ...current.configs[key], base: baseline.configs[key] }));
    const matrix = (kind, title) => {
      const names = Object.keys(current.configs.A.access[kind]);
      return h('div', {}, h('h3', {}, title), table([{ label: '', render: (n) => n },
        ...CONFIGS.map((key) => ({ label: key, render: (n) => {
          const a = current.configs[key].access[kind][n];
          return h('span', { class: a.access === 'none' ? 'muted' : null, title: a.via.join(', ') }, LABEL[a.access]);
        } }))], names, { maxHeight: 400 }));
    };
    const taskBox = h('div', {});
    const drawTasks = () => fill(taskBox, table([
      { key: 'task_id', label: 'Task' },
      { label: 'Covered', render: (r) => h('span', { class: `badge ${r.covered ? 'good' : 'bad'}` }, r.covered ? 'yes' : 'no') },
      { label: 'Access', render: (r) => (r.access ? LABEL[r.access] : '–') }, { key: 'min_calls', label: 'Min calls', num: true },
      { label: 'Why not', render: (r) => r.reasons.join('; ') },
      { label: 'Tool sequences that work', render: (r) => r.usable_sequences.map((sq) => sq.join(' › ')).join('  |  ') },
    ], current.configs[view.config].tasks, { maxHeight: 420 }));

    fill(body, 
      experiment.disabledTools.length ? banner('info', `Disabled tools: ${experiment.disabledTools.join(', ')}. The “change” column compares with all tools enabled.`) : null,
      h('div', { class: 'grid two' },
        h('div', { class: 'card' }, h('h2', {}, 'Coverage by configuration'), table([
          { key: 'key', label: 'Agent' }, { key: 'tool_count', label: 'Tools', num: true },
          { label: 'Task coverage', num: true, render: (r) => pct(r.task_coverage_rate) },
          { label: 'Change', num: true, render: (r) => { const d = Math.round((r.task_coverage_rate - r.base.task_coverage_rate) * 100); return d === 0 ? '–' : `${d > 0 ? '+' : ''}${d} pts`; } },
          { label: 'Objects', num: true, render: (r) => pct(r.object_access_coverage) },
          { label: 'Relationships', num: true, render: (r) => pct(r.relationship_access_coverage) },
          { label: 'Operations', num: true, render: (r) => pct(r.operation_coverage) },
          { label: 'Direct', num: true, render: (r) => r.direct_tasks }, { label: 'Multi-call', num: true, render: (r) => r.multi_call_tasks },
          { label: 'Unsupported', num: true, render: (r) => r.unsupported_tasks.length },
        ], rows), h('p', { class: 'small muted', style: { marginTop: '8px' } }, `${current.task_count} tasks. Direct = answerable with one tool call; multi-call = an earlier call must supply an id.`)),
        h('div', { class: 'card' }, scatterChart('Task coverage by number of tools', rows.map((r) => ({ label: r.key, x: r.tool_count, y: r.task_coverage_rate, color: configColor(r.key) })),
          { xLabel: 'tools available', yLabel: 'task coverage' }),
          h('p', { class: 'small muted' }, 'Coverage is about reach, not tool count: two tools over precomputed profiles reach more than ten domain tools.'))),
      h('div', { class: 'card' }, h('h2', {}, 'What each configuration can reach'),
        h('p', { class: 'small muted' }, 'direct = one tool call · multi = needs an id from an earlier call · – = unreachable. Hover a cell for the tools that provide it.'),
        h('div', { class: 'grid three' }, matrix('objects', 'Object types'), matrix('relationships', 'Relationship types'), matrix('operations', 'Operations'))),
      h('div', { class: 'card' }, h('h2', {}, 'Task coverage'),
        h('div', { class: 'controls' }, field('Configuration', select(CONFIGS, view.config, (v) => { view.config = v; drawTasks(); }))), taskBox));
    drawTasks();
  }
}
