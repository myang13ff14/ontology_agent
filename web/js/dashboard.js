// Evaluation Dashboard: run the benchmark, compare configurations, drill into failures.

import { apiGet, apiPost, h, table, block, details, banner, spinner, select, field, pct, num, experimentBody, CONFIGS, configColor, fill } from './core.js';
import { barChart, scatterChart, stackedBars, heatTable } from './charts.js';
import { traceView } from './trace.js';

const view = { runId: null, config: 'all', failedOnly: true, category: 'all', picked: null,
  form: { configs: new Set(CONFIGS), split: 'test', repeats: 1, limit: '', label: '' }, job: null };
const LAYERS = [{ name: 'ontology', color: 'var(--series-3)' }, { name: 'access_layer', color: 'var(--series-2)' }, { name: 'llm', color: 'var(--series-1)' }];
const LAYER_LABEL = { ontology: 'ontology', access_layer: 'access layer (tools)', llm: 'model' };

export function renderDashboard(root, ctx) {
  const rerender = () => { fill(root, ); renderDashboard(root, ctx); };
  const content = h('div', {}, spinner('Loading evaluation runs…'));
  root.append(h('h1', {}, 'Evaluation Dashboard'),
    h('p', { class: 'muted' }, 'Three dimensions: can the tools do the task (coverage), did the model use them correctly (tool calling), and was the final answer right (end to end).'),
    runForm(ctx, rerender), content);

  apiGet('/evaluation/runs').then(({ runs }) => {
    if (!runs.length) { fill(content, banner('info', 'No evaluation runs yet. Start one above, or run: python -m ontolab.cli evaluate')); return; }
    if (!runs.some((r) => r.run_id === view.runId)) view.runId = runs[0].run_id;
    const label = (r) => `${r.created_at.replace('T', ' ').slice(0, 16)} · ${r.llm.provider}/${r.llm.model} · ${r.settings.split} ×${r.settings.repeats}${r.label ? ` · ${r.label}` : ''}`;
    const picker = h('div', { class: 'controls' }, field('Evaluation run', select(runs.map((r) => ({ value: r.run_id, label: label(r) })), view.runId,
      (v) => { view.runId = v; view.picked = null; rerender(); }, { style: 'max-width: 640px' })));
    const body = h('div', {}, spinner('Loading results…'));
    fill(content, picker, body);
    apiGet(`/evaluation/runs/${view.runId}`).then((data) => drawRun(body, data)).catch((err) => fill(body, banner('bad', err.message)));
  }).catch((err) => fill(content, banner('bad', err.message)));
}

function runForm(ctx, rerender) {
  const f = view.form;
  const status = h('div', {});
  const start = h('button', { class: 'btn primary', disabled: Boolean(view.job), onclick: async () => {
    try {
      const job = await apiPost('/evaluation/run', { configs: [...f.configs].sort(), split: f.split, repeats: Number(f.repeats) || 1,
        limit: f.limit ? Number(f.limit) : null, label: f.label, ...experimentBody() });
      view.job = job;
      poll();
    } catch (err) { fill(status, banner('bad', err.message)); }
  } }, 'Start evaluation');

  async function poll() {
    start.disabled = true;
    const bar = h('progress', { max: 1, value: 0 });
    const text = h('div', { class: 'small muted' }, `Running on ${view.job.llm.provider} / ${view.job.llm.model}…`);
    fill(status, text, bar);
    for (;;) {
      await new Promise((r) => setTimeout(r, 900));
      let state;
      try { state = await apiGet(`/evaluation/jobs/${view.job.job_id}`); } catch (err) { fill(status, banner('bad', err.message)); break; }
      if (state.total) { bar.value = state.done / state.total; text.textContent = `${state.done} / ${state.total} runs on ${view.job.llm.provider} / ${view.job.llm.model}`; }
      if (state.status === 'failed') { fill(status, banner('bad', state.error)); break; }
      if (state.status === 'finished') { view.runId = state.run_id; view.job = null; if (location.hash.startsWith('#dashboard')) rerender(); return; }
    }
    view.job = null;
    start.disabled = false;
  }
  if (view.job) poll();

  return details('Run the benchmark', h('div', { class: 'card' },
    h('div', { class: 'controls' },
      h('div', { class: 'field' }, h('span', {}, 'Configurations'), h('div', { class: 'chips' }, CONFIGS.map((key) => h('button', { class: 'chip', 'aria-pressed': String(f.configs.has(key)),
        onclick: (e) => { f.configs.has(key) ? f.configs.delete(key) : f.configs.add(key); e.target.setAttribute('aria-pressed', String(f.configs.has(key))); } }, key)))),
      field('Split', select([{ value: 'test', label: 'test (held out)' }, { value: 'dev', label: 'dev (for tuning)' }, { value: 'all', label: 'all' }], f.split, (v) => { f.split = v; })),
      field('Runs per question', h('input', { type: 'number', min: 1, max: 10, value: f.repeats, style: { width: '80px' }, onchange: (e) => { f.repeats = e.target.value; } })),
      field('Question limit', h('input', { type: 'number', min: 1, placeholder: 'all', value: f.limit, style: { width: '80px' }, onchange: (e) => { f.limit = e.target.value; } })),
      field('Label', h('input', { type: 'text', value: f.label, placeholder: 'e.g. baseline', onchange: (e) => { f.label = e.target.value; } })),
      start),
    h('p', { class: 'small muted' }, 'Uses the model, disabled tools, edited descriptions and tool-call limit from the sidebar. Tune prompts on dev; report on test.'),
    status), Boolean(view.job));
}

function drawRun(body, data) {
  const summary = data.summary.by_config;
  const keys = CONFIGS.filter((k) => summary[k] && summary[k].runs);
  const rows = keys.map((k) => ({ key: k, ...summary[k] }));
  const s = data.settings;

  const overview = table([
    { key: 'key', label: 'Agent' }, { key: 'tool_count', label: 'Tools', num: true },
    { label: 'Task coverage', num: true, render: (r) => pct(r.coverage.task_coverage_rate) },
    { label: 'Correct tool use | covered', num: true, render: (r) => pct(r.tool_calling.correct_tool_use_rate) },
    { label: 'Success rate', num: true, render: (r) => pct(r.end_to_end.success_rate) },
    { label: 'Mean score /2', num: true, render: (r) => r.end_to_end.mean_score_out_of_2.toFixed(2) },
    { label: 'Completed', num: true, render: (r) => pct(r.end_to_end.task_completion_rate) },
    { label: 'Groundedness', num: true, render: (r) => pct(r.end_to_end.groundedness) },
    { label: 'Hallucination', num: true, render: (r) => pct(r.end_to_end.hallucination_rate) },
    { label: 'Tool calls', num: true, render: (r) => num(r.end_to_end.avg_tool_calls) },
    { label: 'Tokens', num: true, render: (r) => num(r.end_to_end.avg_input_tokens + r.end_to_end.avg_output_tokens, 0) },
    { label: 'Latency', num: true, render: (r) => `${(r.end_to_end.avg_latency_ms / 1000).toFixed(1)}s` },
    { label: 'Consistency', num: true, render: (r) => pct(r.end_to_end.run_to_run_consistency) },
  ], rows);

  const point = (r, x, y) => ({ label: r.key, x, y, color: configColor(r.key) });
  const categories = [...new Set(keys.flatMap((k) => Object.keys(summary[k].by_category)))].sort();
  const levels = [...new Set(keys.flatMap((k) => Object.keys(summary[k].by_complexity)))].sort();
  const failureCategories = [...new Set(keys.flatMap((k) => Object.keys(summary[k].failure_attribution.primary)))];

  fill(body, 
    data.is_mock
      ? banner('bad', 'MOCK MODEL RUN. These numbers come from a fixed policy, not an AI model. They show the pipeline works; they say nothing about how well any model uses the ontology.')
      : banner('info', data.notice),
    h('p', { class: 'small muted' }, `Ontology ${data.ontology_version} · terminology ${data.terminology_version} · profiles ${data.profile_version} · ${s.task_count} questions × ${s.repeats} runs · max ${s.max_tool_calls} tool calls`
      + (s.disabled_tools.length ? ` · disabled tools: ${s.disabled_tools.join(', ')}` : '') + (s.description_overrides.length ? ` · edited descriptions: ${s.description_overrides.join(', ')}` : '')),
    h('div', { class: 'card' }, h('h2', {}, 'Summary by configuration'), overview),
    h('div', { class: 'grid two' },
      h('div', { class: 'card' }, scatterChart('Tool coverage vs tool-calling accuracy',
        rows.map((r) => point(r, r.coverage.task_coverage_rate, r.tool_calling.correct_tool_use_rate)),
        { xLabel: 'task coverage', yLabel: 'correct tool use, given coverage', xPercent: true })),
      h('div', { class: 'card' }, barChart('End-to-end success rate by configuration',
        rows.map((r) => ({ label: r.key, value: r.end_to_end.success_rate, color: configColor(r.key) })), { percent: true })),
      h('div', { class: 'card' }, scatterChart('Success rate vs tool count',
        rows.map((r) => point(r, r.tool_count, r.end_to_end.success_rate)), { xLabel: 'tools available', yLabel: 'success rate' })),
      h('div', { class: 'card' }, scatterChart('Latency vs success rate',
        rows.map((r) => point(r, r.end_to_end.avg_latency_ms / 1000, r.end_to_end.success_rate)), { xLabel: 'average latency (seconds)', yLabel: 'success rate' })),
      h('div', { class: 'card' }, heatTable('Success rate by question complexity (1 simple, 4 hardest)', levels, keys,
        (level, k) => summary[k].by_complexity[level]?.success_rate, { rowHeader: 'Complexity' })),
      h('div', { class: 'card' }, heatTable('Success rate by question category', categories, keys,
        (c, k) => summary[k].by_category[c]?.success_rate, { rowHeader: 'Category' })),
      h('div', { class: 'card' },
        stackedBars('Failed runs by the layer the failure came from', rows.map((r) => ({ label: r.key,
          parts: LAYERS.map((l) => ({ name: LAYER_LABEL[l.name], value: r.failure_attribution.by_layer[l.name] || 0 })) })),
        LAYERS.map((l) => ({ name: LAYER_LABEL[l.name], color: l.color }))),
        h('h3', { style: { marginTop: '12px' } }, 'Primary failure cause (failed runs)'),
        table([{ label: 'Cause', render: (c) => c.replaceAll('_', ' ') }, ...keys.map((k) => ({ label: k, num: true, render: (c) => summary[k].failure_attribution.primary[c] || '' }))], failureCategories)),
      h('div', { class: 'card' }, h('h3', {}, 'Task success = coverage × tool use × interpretation'),
        table([{ key: 'key', label: 'Agent' },
          { label: 'Covered', num: true, render: (r) => pct(r.decomposition.p_covered) },
          { label: 'Correct tool use | covered', num: true, render: (r) => pct(r.decomposition.p_correct_tool_use_given_covered) },
          { label: 'Success | correct tool use', num: true, render: (r) => pct(r.decomposition.p_success_given_correct_tool_use) },
          { label: 'Product', num: true, render: (r) => pct(r.decomposition.product) },
          { label: 'Observed success', num: true, render: (r) => pct(r.decomposition.observed_success_rate) }], rows),
        h('p', { class: 'small muted', style: { marginTop: '8px' } }, 'A conditional breakdown of the observed runs, not a claim that the factors are independent. The product leaves out abstention tasks and uncovered tasks that were answered correctly, so it can differ from the observed rate.'),
        h('h3', { style: { marginTop: '12px' } }, 'Run-to-run consistency'),
        table([{ key: 'key', label: 'Agent' },
          { label: 'Same outcome across repeats', num: true, render: (r) => pct(r.end_to_end.run_to_run_consistency) },
          { label: 'Same tool sequence', num: true, render: (r) => pct(r.end_to_end.tool_sequence_agreement) }], rows),
        s.repeats < 2 ? h('p', { class: 'small muted' }, 'Needs at least 2 runs per question.') : null)),
    h('div', { class: 'card' }, h('h2', {}, 'Tool-calling accuracy'), table([{ key: 'key', label: 'Agent' },
      { label: 'Runs judged', num: true, render: (r) => r.tool_calling.judged_runs },
      { label: 'Tool selection', num: true, render: (r) => pct(r.tool_calling.tool_selection_accuracy) },
      { label: 'Required-call recall', num: true, render: (r) => pct(r.tool_calling.required_tool_recall) },
      { label: 'Unnecessary calls', num: true, render: (r) => num(r.tool_calling.unnecessary_tool_calls, 2) },
      { label: 'Argument accuracy', num: true, render: (r) => pct(r.tool_calling.argument_accuracy) },
      { label: 'Sequencing', num: true, render: (r) => pct(r.tool_calling.sequencing_correctness) },
      { label: 'Multi-step success', num: true, render: (r) => pct(r.tool_calling.multi_step_orchestration_success) },
      { label: 'Grounded interpretation', num: true, render: (r) => pct(r.tool_calling.grounded_interpretation_accuracy) },
      { label: 'Other valid routes', num: true, render: (r) => r.tool_calling.alternative_sequences_accepted }], rows),
    h('p', { class: 'small muted', style: { marginTop: '8px' } }, 'Judged only on tasks the tools can do. A correct, grounded answer reached by an unlisted route counts as a valid route, not an error.')),
    results(data));
}

function results(data) {
  const box = h('div', { class: 'card' });
  const categories = [...new Set(data.results.map((r) => r.category))].sort();
  const keys = CONFIGS.filter((k) => data.summary.by_config[k]);
  const draw = () => {
    const rows = data.results.filter((r) => (view.config === 'all' || r.config === view.config) && (!view.failedOnly || !r.success)
      && (view.category === 'all' || r.category === view.category));
    const id = (r) => `${r.task_id}|${r.config}|${r.repeat}`;
    const detail = h('div', {});
    const open = async (r) => {
      view.picked = id(r);
      fill(detail, spinner('Loading trace…'));
      try { fill(detail, resultDetail(await apiGet(`/evaluation/runs/${data.run_id}/trace`, { task_id: r.task_id, config: r.config, repeat: r.repeat }))); }
      catch (err) { fill(detail, banner('bad', err.message)); }
    };
    fill(box, h('h2', {}, 'Individual benchmark results'),
      h('div', { class: 'controls' },
        field('Configuration', select(['all', ...keys], view.config, (v) => { view.config = v; draw(); })),
        field('Category', select(['all', ...categories], view.category, (v) => { view.category = v; draw(); })),
        h('label', { style: { display: 'flex', gap: '6px', alignItems: 'center' } }, h('input', { type: 'checkbox', checked: view.failedOnly, onchange: (e) => { view.failedOnly = e.target.checked; draw(); } }), 'Failed runs only'),
        h('span', { class: 'small muted' }, `${rows.length} of ${data.results.length} runs · click a row to inspect it`)),
      table([{ key: 'task_id', label: 'Task' }, { key: 'config', label: 'Agent' }, { key: 'repeat', label: 'Run', num: true },
        { label: 'Category', render: (r) => r.category.replaceAll('_', ' ') }, { key: 'complexity', label: 'Cx', num: true },
        { label: 'Covered', render: (r) => (r.covered ? 'yes' : (r.covered_in_practice ? 'in practice' : 'no')) },
        { label: 'Score', num: true, render: (r) => h('span', { class: `badge ${r.score === 2 ? 'good' : r.score === 1 ? 'warn' : 'bad'}` }, `${r.score}/2`) },
        { label: 'Primary failure', render: (r) => (r.failure ? r.failure.primary.replaceAll('_', ' ') : '') },
        { label: 'Tools called', render: (r) => r.tool_use.called.join(' › ') },
        { label: 'Answer', render: (r) => (r.final_answer || '').slice(0, 110) }],
      rows, { onRow: open, selected: (r) => id(r) === view.picked, maxHeight: 330 }), detail);
    const again = rows.find((r) => id(r) === view.picked);
    if (again) open(again);
  };
  draw();
  return box;
}

function resultDetail(r) {
  return h('div', { class: 'grid two', style: { marginTop: '14px' } },
    h('div', {},
      h('h2', {}, `${r.task_id} · agent ${r.config} · run ${r.repeat}`),
      h('p', {}, r.question),
      h('p', {}, h('span', { class: `badge ${r.score === 2 ? 'good' : r.score === 1 ? 'warn' : 'bad'}` }, `score ${r.score}/2`), ' ',
        h('span', { class: 'muted' }, `${r.category.replaceAll('_', ' ')} · complexity ${r.complexity} · covered: ${r.covered ? 'yes' : (r.covered_in_practice ? 'in practice only' : 'no')}`)),
      r.failure ? h('div', {}, banner('bad', `Primary cause: ${r.failure.primary.replaceAll('_', ' ')} (${LAYER_LABEL[r.failure.layer]}). ${r.failure.detail}`),
        r.failure.contributing.map((c) => h('p', { class: 'small muted' }, `Also: ${c.category.replaceAll('_', ' ')} · ${c.detail}`))) : null,
      details('Answer check', block(r.answer, 240), true),
      details('Tool-use check', block(r.tool_use, 240)),
      details('Evidence and grounding', block({ evidence: r.evidence, grounding: r.grounding }, 240))),
    h('div', {}, h('h2', {}, 'Execution trace'), traceView(r.trace)));
}
