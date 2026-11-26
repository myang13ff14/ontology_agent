// Agent Playground: ask one question of one or several agent configurations, side by side.

import { apiPost, h, select, field, banner, spinner, details, block, experimentBody, fill } from './core.js';
import { traceView } from './trace.js';

const view = { question: '', configs: new Set(['B']), runs: [], busy: false, autoRan: false };

export function renderPlayground(root, ctx) {
  const params = new URLSearchParams(location.hash.split('?')[1] || '');
  if (!view.autoRan && params.get('q')) {
    view.question = params.get('q');
    if (params.get('configs')) view.configs = new Set(params.get('configs').split(',').filter((c) => ctx.agents.some((a) => a.key === c)));
  }
  const rerender = () => { fill(root, ); renderPlayground(root, ctx); };
  const question = h('textarea', { rows: 3, placeholder: 'Ask about troubleshooting knowledge or support operations…',
    oninput: (e) => { view.question = e.target.value; ask.disabled = view.busy || !view.question.trim() || !view.configs.size; } }, view.question);
  const ask = h('button', { class: 'btn primary', disabled: view.busy || !view.question.trim() || !view.configs.size, onclick: run },
    view.busy ? 'Running…' : 'Ask');

  async function run() {
    view.busy = true;
    view.runs = [...view.configs].sort().map((key) => ({ key, trace: null, error: null }));
    rerender();
    await Promise.all(view.runs.map(async (item) => {
      try { item.trace = await apiPost('/agents/run', { question: view.question, config: item.key, ...experimentBody() }); }
      catch (err) { item.error = err.message; }
      if (location.hash.startsWith('#playground')) rerender();
    }));
    view.busy = false;
    if (location.hash.startsWith('#playground')) rerender();
  }

  root.append(h('h1', {}, 'Agent Playground'),
    h('p', { class: 'muted' }, 'The model chooses the tools; the runtime validates and executes each call. Pick several configurations to compare them on the same question.'),
    h('div', { class: 'card' },
      h('div', { class: 'controls' }, field('Example question from the benchmark', select(
        [{ value: '', label: '(type your own)' }, ...ctx.benchmark.map((t) => ({ value: t.question, label: `${t.id} · ${t.question}` }))], '',
        (v) => { if (v) { view.question = v; rerender(); } }, { style: 'max-width: 560px' }))),
      question,
      h('h3', { style: { marginTop: '10px' } }, 'Agent configurations'),
      h('div', { class: 'chips' }, ctx.agents.map((a) => h('button', { class: 'chip', 'aria-pressed': String(view.configs.has(a.key)), title: a.description,
        onclick: () => { view.configs.has(a.key) ? view.configs.delete(a.key) : view.configs.add(a.key); rerender(); } }, `${a.key} · ${a.name}`))),
      h('div', { class: 'controls', style: { marginTop: '12px', marginBottom: 0 } }, ask,
        h('span', { class: 'small muted' }, 'Uses the model, disabled tools, edited descriptions and tool-call limit from the sidebar.'))));

  if (view.runs.length) {
    root.append(h('div', { class: 'cols' }, view.runs.map((item) => {
      const agent = ctx.agents.find((a) => a.key === item.key);
      return h('div', { class: 'card' },
        h('h2', {}, `${agent.key} · ${agent.name}`),
        item.error ? banner('bad', item.error)
          : item.trace ? h('div', {}, h('p', { class: 'small muted' }, `${item.trace.llm.provider} / ${item.trace.llm.model} · ontology ${item.trace.ontology_version}`), traceView(item.trace))
            : spinner('Running…'));
    })));
  }
  root.append(h('div', { class: 'card' }, h('h2', {}, 'Configurations'), ctx.agents.map((a) => details(`${a.key} · ${a.name}`, () => h('div', {},
    h('p', {}, a.description), h('p', { class: 'small muted' }, `Tools: ${a.tools.join(', ')}`), block(a.system_prompt, 300))))));

  if (!view.autoRan && params.get('run') && view.question.trim()) { view.autoRan = true; run(); }
  view.autoRan = true;
}
