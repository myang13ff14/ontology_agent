// Agent Playground: ask one question of one or several agent configurations, side by side,
// then keep talking to each of them. Each column is one conversation with its own memory.

import { apiPost, h, select, field, banner, spinner, details, block, richText, experimentBody, fill } from './core.js';
import { traceView } from './trace.js';

// columns: [{ key, conversationId, turns: [{question, trace, error}], busy, draft }]
const view = { question: '', configs: new Set(['B']), columns: [], autoRan: false };

async function ask(column, question, rerender) {
  column.busy = true;
  const turn = { question, trace: null, error: null };
  column.turns.push(turn);
  rerender();
  try {
    turn.trace = await apiPost('/agents/run', { question, config: column.key, conversation_id: column.conversationId, ...experimentBody() });
    column.conversationId = turn.trace.conversation?.conversation_id || column.conversationId;
  } catch (err) {
    turn.error = err.message;
  }
  column.busy = false;
  if (location.hash.startsWith('#playground')) rerender();
}

export function renderPlayground(root, ctx) {
  const params = new URLSearchParams(location.hash.split('?')[1] || '');
  if (!view.autoRan && params.get('q')) {
    view.question = params.get('q');
    if (params.get('configs')) view.configs = new Set(params.get('configs').split(',').filter((c) => ctx.agents.some((a) => a.key === c)));
  }
  const rerender = () => { fill(root); renderPlayground(root, ctx); };
  const anyBusy = view.columns.some((c) => c.busy);
  const startButton = h('button', { class: 'btn primary', disabled: anyBusy || !view.question.trim() || !view.configs.size, onclick: start },
    anyBusy ? 'Running…' : 'Ask (new conversation)');
  const question = h('textarea', { rows: 3, placeholder: 'Ask about troubleshooting knowledge or support operations…',
    oninput: (e) => { view.question = e.target.value; startButton.disabled = anyBusy || !view.question.trim() || !view.configs.size; } }, view.question);

  function start() {
    // A new question from the top starts fresh conversations: nothing is carried over.
    view.columns = [...view.configs].sort().map((key) => ({ key, conversationId: null, turns: [], busy: false, draft: '' }));
    rerender();
    view.columns.forEach((column) => ask(column, view.question.trim(), rerender));
  }

  root.append(h('h1', {}, 'Agent Playground'),
    h('p', { class: 'muted' }, 'The model chooses the tools; the runtime validates and executes each call. Pick several configurations to compare them on the same question, then follow up: each one remembers its own conversation.'),
    h('div', { class: 'card' },
      h('div', { class: 'controls' }, field('Example question from the benchmark', select(
        [{ value: '', label: '(type your own)' }, ...ctx.benchmark.map((t) => ({ value: t.question, label: `${t.id} · ${t.question}` }))], '',
        (v) => { if (v) { view.question = v; rerender(); } }, { style: 'max-width: 560px' }))),
      question,
      h('h3', { style: { marginTop: '10px' } }, 'Agent configurations'),
      h('div', { class: 'chips' }, ctx.agents.map((a) => h('button', { class: 'chip', 'aria-pressed': String(view.configs.has(a.key)), title: a.description,
        onclick: () => { view.configs.has(a.key) ? view.configs.delete(a.key) : view.configs.add(a.key); rerender(); } }, `${a.key} · ${a.name}`))),
      h('div', { class: 'controls', style: { marginTop: '12px', marginBottom: 0 } }, startButton,
        h('span', { class: 'small muted' }, 'Uses the model, disabled tools, edited descriptions and tool-call limit from the sidebar.'))));

  if (view.columns.length) {
    root.append(h('div', { class: 'cols' }, view.columns.map((column) => {
      const agent = ctx.agents.find((a) => a.key === column.key);
      const last = column.turns[column.turns.length - 1];
      const memory = [...column.turns].reverse().find((t) => t.trace?.conversation)?.trace.conversation;
      const followUp = h('textarea', { rows: 2, placeholder: 'Follow up, or answer its question…', disabled: column.busy,
        oninput: (e) => { column.draft = e.target.value; send.disabled = column.busy || !column.draft.trim(); },
        onkeydown: (e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && column.draft.trim() && !column.busy) go(); } }, column.draft);
      const go = () => { const text = column.draft.trim(); column.draft = ''; ask(column, text, rerender); };
      const send = h('button', { class: 'btn primary', disabled: column.busy || !column.draft.trim(), onclick: go }, 'Send follow-up');
      const turnView = (turn, index) => {
        const isLast = turn === last;
        const body = turn.error ? banner('bad', turn.error) : (turn.trace ? (isLast ? traceView(turn.trace)
          : h('div', {}, richText(turn.trace.final_answer || 'No answer.'), details(`Trace of turn ${index + 1} (${turn.trace.tool_calls.length} tool calls)`, () => traceView(turn.trace))))
          : spinner('Running…'));
        return h('div', { style: { marginBottom: '14px' } },
          h('p', {}, h('span', { class: 'badge' }, `turn ${index + 1}`), ' ', h('b', {}, turn.question)), body);
      };
      return h('div', { class: 'card' },
        h('h2', {}, `${agent.key} · ${agent.name}`),
        last?.trace ? h('p', { class: 'small muted' }, `${last.trace.llm.provider} / ${last.trace.llm.model} · ontology ${last.trace.ontology_version}`) : null,
        column.turns.map(turnView),
        memory ? h('p', { class: 'small muted' }, `Memory: remembering ${memory.remembered_turns} of ${memory.turn} turn${memory.turn === 1 ? '' : 's'} `
          + `(window ${memory.window}${memory.dropped_turns ? `, ${memory.dropped_turns} oldest dropped` : ''}) · ${memory.remembered_messages} messages replayed next time`) : null,
        followUp,
        h('div', { class: 'controls', style: { marginTop: '8px', marginBottom: 0 } }, send,
          h('button', { class: 'btn', disabled: column.busy, title: 'Forget this conversation and start over',
            onclick: () => { column.conversationId = null; column.turns = []; column.draft = ''; rerender(); } }, 'Forget'),
          h('span', { class: 'small muted' }, '⌘/Ctrl + Enter to send')));
    })));
  }
  root.append(h('div', { class: 'card' }, h('h2', {}, 'Configurations'), ctx.agents.map((a) => details(`${a.key} · ${a.name}`, () => h('div', {},
    h('p', {}, a.description), h('p', { class: 'small muted' }, `Tools: ${a.tools.join(', ')}`), block(a.system_prompt, 300))))));

  if (!view.autoRan && params.get('run') && view.question.trim()) { view.autoRan = true; start(); }
  view.autoRan = true;
}
