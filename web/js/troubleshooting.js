// Troubleshooting: work a support case on the diagram-based ontology. The left side is the
// conversation with the customer; the right side is the case record the agent writes as it goes.

import { apiGet, apiPost, h, table, block, details, banner, spinner, select, field, richText, experiment, fill } from './core.js';
import { traceView } from './trace.js';
import { graphView, graphLegend, typeStyles } from './graph.js';

const WORKSPACE = 'troubleshooting';
const EXAMPLES = ['My computer screen is dead', "It won't start", 'It gets stuck on the Lenovo logo with the spinning circle',
  'Everything takes ages to open', "There's a problem with my battery, it keeps running out of power", 'Is my laptop still under warranty?'];
const view = { conversationId: null, caseId: null, turns: [], busy: false, draft: '', device: '', browsing: null, meta: null, error: null };

const VERDICT_TONE = { confirmed: 'good', supported: 'accent', candidate: 'neutral', ruled_out: 'bad' };
const STATUS_TONE = { resolved: 'good', in_progress: 'accent', escalated: 'warn', confirmed: 'good', narrowed: 'accent', candidates: 'neutral', superseded: 'neutral' };
const pretty = (text) => String(text).replaceAll('_', ' ');

// The order the agent actually took the steps in, shown only when it differs from the written order.
function taken(feedback) {
  const order = feedback.map((x) => x.step);
  const reordered = order.some((n, i) => i > 0 && n < order[i - 1]);
  return reordered ? h('p', { class: 'small muted' }, `Order chosen by the agent: ${order.join(', ')}`) : null;
}

function casePanel(state, live) {
  if (!state) {
    return h('div', { class: 'card' }, h('h2', {}, 'Case record'),
      h('p', { class: 'muted' }, 'Nothing yet. The record appears here as soon as the customer describes the problem: candidates, each check result, every assessment and the chosen solution.'));
  }
  const conditions = (c) => h('div', { class: 'cond' },
    c.conditions.met.map((x) => h('div', {}, '✓ ', h('b', {}, x.check), ` = ${x.actual}`)),
    c.conditions.not_yet_checked.map((x) => h('div', {}, '· ', x.check, ` (needs ${x.expect})`)),
    c.conditions.contradicted.map((x) => h('div', {}, '✗ ', h('b', {}, x.check), ` = ${x.actual}, needs ${x.expect}`)));
  const graphBox = details('Evidence graph for this case', () => {
    const box = h('div', {}, spinner('Loading graph…'));
    apiGet('/ontology/graph', { workspace: WORKSPACE, focus: state.case_id, depth: 2 }).then((g) => {
      const styles = typeStyles(new Set(g.nodes.map((n) => n.node_type)));
      fill(box, graphLegend(new Set(g.nodes.map((n) => n.node_type)), styles), graphView(g.nodes, g.edges, { height: 340, styles, selected: state.case_id }));
    }).catch((err) => fill(box, banner('bad', err.message)));
    return box;
  });
  return h('div', { class: 'card' },
    h('h2', {}, `${state.case_id} `, h('span', { class: `badge ${STATUS_TONE[state.status] || 'neutral'}` }, pretty(state.status)),
      live ? null : h('span', { class: 'small muted' }, '  viewing a stored case')),
    h('p', {}, h('span', { class: 'muted' }, 'Customer said: '), `“${state.raw_description}”`),
    state.device ? h('p', { class: 'small muted' }, `Device: ${state.device.name}`) : null,

    h('h3', {}, 'Current candidates'),
    state.current_candidates.length ? table([
      { label: 'Symptom', render: (c) => c.name },
      { label: 'Verdict', render: (c) => h('span', { class: `badge ${VERDICT_TONE[c.verdict]}` }, pretty(c.verdict)) },
      { label: 'Distinguishing conditions', render: conditions },
    ], state.current_candidates) : h('p', { class: 'muted' }, 'No assessment recorded yet.'),

    state.check_in_progress ? h('div', {},
      h('h3', { style: { marginTop: '12px' } }, 'Check in progress: ', state.check_in_progress.check,
        h('span', { class: 'small muted' }, `  ${state.check_in_progress.feedback.length} of ${state.check_in_progress.steps.length} steps answered`)),
      table([{ label: 'Step', render: (x) => String(x.step) }, { key: 'instruction', label: 'Agent says' },
        { label: 'Customer reply', render: (x) => (x.status === 'has_feedback' ? x.feedback : h('span', { class: 'muted' }, 'waiting')) }],
      state.check_in_progress.steps),
      taken(state.check_in_progress.feedback)) : null,

    h('h3', { style: { marginTop: '12px' } }, `Check results (${state.check_results.length})`),
    state.check_results.length ? h('div', {}, state.check_results.map((r) => h('div', { class: 'check-result' },
      h('div', {}, h('b', {}, r.check), ' ', h('code', {}, r.result), h('span', { class: 'small muted' }, `  ${r.evidence}`)),
      (r.step_feedback || []).length ? details(`Steps and customer replies (${r.step_feedback.length})`, () => h('div', {},
        table([{ label: 'Step', render: (x) => String(x.step) }, { key: 'instruction', label: 'Agent says' },
          { label: 'Customer reply', render: (x) => (x.feedback ?? h('span', { class: 'muted' }, `skipped: ${x.skipped_because}`)) }],
        [...r.step_feedback].sort((a, b) => a.step - b.step)), taken(r.step_feedback.filter((x) => x.feedback != null)))) : null)))
      : h('p', { class: 'muted' }, 'None yet.'),

    h('h3', { style: { marginTop: '12px' } }, `Assessment history (${state.assessments.length})`),
    state.assessments.length ? h('ul', { class: 'timeline' }, [...state.assessments].reverse().map((a) =>
      h('li', { class: a.status === 'superseded' ? 'old' : 'current' },
        h('div', {}, h('b', {}, `#${a.sequence} `), h('span', { class: `badge ${STATUS_TONE[a.status] || 'neutral'}` }, pretty(a.status)),
          h('span', { class: 'small muted' }, `  based on ${a.based_on.length} check result${a.based_on.length === 1 ? '' : 's'}`
            + (a.supersedes.length ? ` · replaces #${a.sequence - 1}` : ''))),
        h('div', {}, a.evidence),
        h('div', { class: 'small muted' }, a.candidates.map((c) => `${c.name}: ${pretty(c.verdict)}`).join(' · '))))) : h('p', { class: 'muted' }, 'None yet.'),

    h('h3', { style: { marginTop: '12px' } }, 'Solution'),
    state.selected_solution ? h('p', {}, h('b', {}, state.selected_solution.name), ' ', h('span', { class: 'badge good' }, pretty(state.selected_solution.outcome)))
      : h('p', { class: 'muted' }, state.confirmed_symptom ? 'Symptom confirmed; no solution selected yet.' : 'A solution can be chosen only after a symptom is confirmed.'),
    graphBox);
}

export function renderTroubleshooting(root, ctx) {
  const rerender = () => { if (location.hash.startsWith('#troubleshooting')) { fill(root); renderTroubleshooting(root, ctx); } };
  if (!view.meta) {
    root.append(spinner('Loading the troubleshooting ontology…'));
    Promise.all([apiGet('/troubleshooting/status'), apiGet('/troubleshooting/devices'), apiGet('/troubleshooting/cases'), apiGet('/troubleshooting/tools')])
      .then(([status, devices, cases, tools]) => { view.meta = { status, devices: devices.devices, cases: cases.cases, tools }; rerender(); })
      .catch((err) => fill(root, banner('bad', err.message)));
    return;
  }
  const { meta } = view;
  const liveState = view.turns.length ? [...view.turns].reverse().find((t) => t.caseState)?.caseState : null;
  const started = view.turns.length > 0;

  async function send(text) {
    text = text.trim();
    if (!text || view.busy) return;
    view.busy = true; view.error = null; view.draft = ''; view.browsing = null;
    const turn = { question: text, trace: null, caseState: null };
    view.turns.push(turn);
    rerender();
    try {
      const body = { message: text, conversation_id: view.conversationId, device_id: view.conversationId ? null : (view.device || null) };
      if (experiment.llmProvider) { body.llm_provider = experiment.llmProvider; if (experiment.llmModel) body.llm_model = experiment.llmModel; }
      const data = await apiPost('/troubleshooting/chat', body);
      view.conversationId = data.conversation_id; view.caseId = data.case_id;
      data.trace.subgraph.workspace = WORKSPACE;
      turn.trace = data.trace; turn.caseState = data.case;
      view.meta.cases = (await apiGet('/troubleshooting/cases')).cases;
    } catch (err) {
      view.turns.pop(); view.error = err.message; view.draft = text;
    }
    view.busy = false;
    rerender();
  }
  const newCase = () => { Object.assign(view, { conversationId: null, caseId: null, turns: [], draft: '', browsing: null, error: null }); rerender(); };

  const input = h('textarea', { rows: 2, disabled: view.busy, placeholder: started ? 'Reply as the customer…' : 'Describe the problem the way a customer would…',
    oninput: (e) => { view.draft = e.target.value; sendButton.disabled = view.busy || !view.draft.trim(); },
    onkeydown: (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(view.draft); } } }, view.draft);
  const sendButton = h('button', { class: 'btn primary', disabled: view.busy || !view.draft.trim(), onclick: () => send(view.draft) }, view.busy ? 'Working…' : 'Send');

  const chat = h('div', { class: 'chat' }, view.turns.flatMap((turn) => [
    h('div', { class: 'bubble customer' }, turn.question),
    turn.trace ? h('div', { class: 'bubble agent' }, richText(turn.trace.final_answer || `(${turn.trace.stop_reason}${turn.trace.error ? `: ${turn.trace.error}` : ''})`)) : h('div', { class: 'bubble agent' }, spinner('Working on the case…')),
    turn.trace ? details(`What the agent did: ${turn.trace.tool_calls.map((c) => c.tool).join(' › ') || 'no tool calls'}`, () => traceView(turn.trace)) : null,
  ].filter(Boolean)));
  chat.querySelectorAll('details').forEach((d) => d.classList.add('turn-meta'));
  queueMicrotask(() => { chat.scrollTop = chat.scrollHeight; });

  const conversation = h('div', { class: 'card' },
    h('h2', {}, started ? `Conversation · ${view.caseId || 'opening case…'}` : 'New case'),
    !started ? h('div', {},
      h('div', { class: 'controls' }, field('Device (optional)', select([{ value: '', label: 'not specified' }, ...meta.devices.map((d) => ({ value: d.device_id, label: d.name }))], view.device, (v) => { view.device = v; }))),
      h('h3', {}, 'Try a customer opening'),
      h('div', { class: 'chips', style: { marginBottom: '10px' } }, EXAMPLES.map((text) => h('button', { class: 'chip', onclick: () => send(text) }, text)))) : chat,
    view.error ? banner('bad', view.error) : null,
    input,
    h('div', { class: 'controls', style: { marginTop: '8px', marginBottom: 0 } }, sendButton,
      started ? h('button', { class: 'btn', disabled: view.busy, onclick: newCase }, 'New case') : null,
      h('span', { class: 'small muted' }, 'You play the customer. Enter sends, Shift+Enter adds a line.')));

  const browsing = view.browsing;
  const record = browsing ? casePanel(browsing, false) : casePanel(liveState, true);

  const stored = h('div', { class: 'card' }, h('h2', {}, 'Recorded cases'),
    h('p', { class: 'small muted' }, 'The example cases shipped with the ontology, plus every case opened here. Click one to read its record.'),
    table([{ key: 'case_id', label: 'Case' }, { label: 'Status', render: (c) => h('span', { class: `badge ${STATUS_TONE[c.status] || 'neutral'}` }, pretty(c.status)) },
      { key: 'raw_description', label: 'Customer said' }, { label: 'Opened', render: (c) => (c.opened_at || '').replace('T', ' ') },
      { label: 'Origin', render: (c) => (c.source === 'runtime' ? 'lab' : 'example') }],
    meta.cases, { maxHeight: 240, selected: (c) => browsing && c.case_id === browsing.case_id,
      onRow: async (c) => { try { view.browsing = await apiGet(`/troubleshooting/cases/${c.case_id}`); } catch (err) { view.error = err.message; } rerender(); } }),
    h('div', { class: 'controls', style: { marginTop: '10px', marginBottom: 0 } },
      browsing ? h('button', { class: 'btn', onclick: () => { view.browsing = null; rerender(); } }, 'Back to the live case') : null,
      h('button', { class: 'btn', disabled: view.busy, title: 'Restore the ontology file: removes cases opened here',
        onclick: async () => { await apiPost('/troubleshooting/reset'); view.meta = null; newCase(); } }, 'Reset to the example cases')));

  fill(root, h('h1', {}, 'Troubleshooting'),
    h('p', { class: 'muted' }, `A Lenovo support agent works a hardware, software or warranty case on the diagram-based ontology (${meta.status.ontology.name} v${meta.status.ontology.version}; synthetic and illustrative, not official Lenovo guidance). `
      + 'It simulates a voice agent: it leads the customer through each check one step per turn, choosing the order itself, and stores every reply. '
      + 'It may confirm a symptom only when recorded check results meet its conditions, and offer a solution only when its condition holds: both are enforced by code.'),
    ctx.status.llm.is_mock && !experiment.llmProvider ? banner('warn', 'The server is on the mock model, which cannot hold a troubleshooting conversation. Set an OpenAI model in .env.') : null,
    h('div', { class: 'grid two' }, conversation, record), stored,
    h('div', { class: 'card' }, h('h2', {}, 'How this agent is set up'),
      details(`Tools (${meta.tools.tools.length})`, () => table([{ label: 'Tool', render: (t) => h('code', {}, t.name) }, { key: 'description', label: 'What it does' }], meta.tools.tools)),
      details('System prompt', () => block(meta.tools.system_prompt, 360))));
}
