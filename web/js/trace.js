// Renders one agent run: answer, metrics, tool trace, generated SQL, traversed subgraph.

import { apiPost, h, block, details, banner, spinner, richText, num, fill } from './core.js';
import { graphView, graphLegend, typeStyles } from './graph.js';

export function traceView(trace) {
  const m = trace.metrics;
  const tile = (name, value) => h('div', { class: 'tile' }, h('div', { class: 'value' }, value), h('div', { class: 'name' }, name));
  return h('div', {},
    trace.is_mock ? h('p', { class: 'small muted' }, 'Mock model: this is not a model answer.') : null,
    trace.stop_reason !== 'answered' ? banner('warn', `Stopped: ${trace.stop_reason}${trace.error ? ` · ${trace.error}` : ''}`) : null,
    richText(trace.final_answer || 'No answer.'),
    h('div', { class: 'tiles' },
      tile(m.tool_error_count ? `tool calls (${m.tool_error_count} errors)` : 'tool calls', m.tool_call_count),
      tile('tokens in / out', `${num(m.input_tokens, 0)} / ${num(m.output_tokens, 0)}`),
      tile('latency', `${(m.latency_ms / 1000).toFixed(1)}s`),
      tile('model turns', m.llm_turns)),
    details(`Tool execution trace (${trace.tool_calls.length} calls)`, () => h('div', {},
      h('p', { class: 'small muted' }, `Tools offered: ${trace.tools_available.join(', ')}`),
      trace.tool_calls.map((call) => h('div', { style: { marginBottom: '10px' } },
        h('p', {}, h('b', {}, `${call.index}. `), h('code', {}, call.tool), ' ',
          h('span', { class: `badge ${call.ok ? 'good' : 'bad'}` }, call.ok ? 'ok' : call.error.code),
          h('span', { class: 'small muted' }, ` ${call.duration_ms} ms · ontology ${call.ontology_version}`)),
        h('div', { class: 'small muted' }, 'Arguments'), block(call.arguments, 140),
        h('div', { class: 'small muted', style: { marginTop: '4px' } }, call.ok ? 'Returned data' : 'Error'), block(call.ok ? call.result : call.error, 240))))),
    trace.generated_sql.length ? details(`Generated SQL (${trace.generated_sql.length})`, () => h('div', {}, trace.generated_sql.map((sql) => block(sql, 200)))) : null,
    trace.subgraph.node_ids.length ? details(`Traversed ontology subgraph (${trace.subgraph.node_ids.length} objects, ${trace.subgraph.edge_ids.length} relationships)`, () => {
      const box = h('div', {}, spinner('Loading subgraph…'));
      apiPost('/ontology/subgraph', trace.subgraph).then((sub) => {
        const styles = typeStyles(new Set(sub.nodes.map((n) => n.node_type)));
        fill(box, graphLegend(new Set(sub.nodes.map((n) => n.node_type)), styles),
          sub.nodes.length <= 120 ? graphView(sub.nodes, sub.edges, { height: 320, styles }) : h('p', { class: 'muted' }, 'Too large to draw.'),
          h('p', { class: 'small muted' }, sub.nodes.map((n) => `${n.canonical_name} (${n.node_id})`).join(' · ')));
      }).catch((err) => fill(box, banner('bad', err.message)));
      return box;
    }) : null);
}
