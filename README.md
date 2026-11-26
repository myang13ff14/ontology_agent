# Ontology Agent Lab

A local, end-to-end research prototype of an **ontology-grounded AI agent**, built to study how an
agent reaches an ontology through different access mechanisms, and why it succeeds or fails.

It reimplements the architecture of Snowflake's engineering post
[Ontology-grounded Reasoning with Cortex Agents](https://www.snowflake.com/en/blog/engineering/ontology-grounded-cortex-agents/)
without Snowflake: DuckDB instead of Snowflake tables, recursive SQL instead of stored procedures,
a small semantic layer instead of Cortex Analyst, and any tool-calling LLM instead of Cortex Agents.

> **All data is synthetic.** The laptop-support ontology, products, procedures, rules, articles and
> the 2,000 service cases are invented for demonstration.

> **No model results are included.** Without credentials the lab runs a *mock model*: a fixed policy
> that exercises the pipeline. Mock runs are labelled everywhere and say nothing about how well a
> real model uses the ontology. Configure a model (below) to produce real results.

## Research questions

1. How accurately can an LLM select and invoke the right ontology tools?
2. How completely do the available tools expose what the ontology can do?
3. What is the trade-off between tool coverage and orchestration complexity?
4. Does precomputing ontology knowledge beat traversing the graph at run time?

The lab answers them by holding data and model constant and varying only the access mechanism,
across five agent configurations, and by scoring three things separately: whether the tools *could*
do a task (coverage), whether the model *used* them correctly, and whether the final answer was right.

## Quick start

Requires Python 3.10+.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

.venv/bin/python -m ontolab.cli bootstrap          # ingest ontology, generate data, build profiles
.venv/bin/uvicorn ontolab.api.main:app --port 8600  # backend + web interface
```

Open **http://localhost:8600**. API documentation is at http://localhost:8600/docs.

```bash
.venv/bin/python -m pytest -q                                   # 74 tests, about 4 seconds
.venv/bin/python -m ontolab.cli coverage                        # tool coverage, no model needed
.venv/bin/python -m ontolab.cli ask "What should the agent do after a power reset fails?" --config B
.venv/bin/python -m ontolab.cli evaluate --split test --repeats 3
```

DuckDB allows one process at a time, so the `ontolab.cli` commands need the backend to be stopped;
while it is running, use the web interface or the API for the same operations.

### Choosing a model

Copy `.env.example` to `.env` and fill in one block. Nothing else changes.

| `LLM_PROVIDER` | What it uses | Needs |
|---|---|---|
| `mock` (default) | Fixed policy, no AI | nothing |
| `openai` | Any OpenAI-compatible chat-completions API | `LLM_API_KEY`, `LLM_MODEL` (`LLM_BASE_URL` for other providers) |
| `anthropic` | Claude through the official SDK | `ANTHROPIC_API_KEY` (model defaults to `claude-opus-5-5`) |
| `local` | Ollama / LM Studio at `http://localhost:11434/v1` | `LLM_MODEL` |

The model must support tool (function) calling. The web interface can also switch provider per
request, which is how you change the LLM while keeping the tool registry fixed.

## Architecture

```
web/  (JavaScript, no build step)          five sections, served by the backend
  │  HTTP
ontolab/api        FastAPI service
  ├─ agents/       five configurations + the tool-calling runtime
  ├─ evaluation/   benchmark, coverage analysis, scoring, failure attribution, runner
  ├─ llm/          provider-neutral clients: OpenAI-compatible, Anthropic, mock
  ├─ tools/        tool registry + domain, generic, semantic and retrieval tools     ← Layer 4
  ├─ graphrag/     flattened profiles, hybrid retrieval, terminology mappings
  ├─ ontology/     definition format, ingestion, SQL graph store, rule evaluation    ← Layer 3
  ├─ semantic/     governed semantic view compiled to SQL                            ← Layer 2
  └─ sample/       the laptop-support ontology and synthetic operational data        ← Layer 1
DuckDB  (runs/ontolab.duckdb)
```

Ontology, tool registry, agent orchestration and evaluation are separate packages with one-way
dependencies; the generic engine never imports the sample domain (a test enforces that the generic
tools and graph store contain no domain words).

### Layer 1: operational data

`service_case`, `diagnostic_execution`, `work_order`. Each synthetic case is produced by *walking the
ontology itself*: the simulator picks a symptom, follows its checklist, samples outcomes, follows
`LEADS_TO` branches and respects decision rules. The operational data is therefore consistent with
the graph, and seeded, so it is reproducible.

### Layer 2: semantic view

`data/semantic_view.yaml` declares logical tables, joins, dimensions, facts and metrics, each metric
with an explicit formula, numerator and denominator (Average Handle Time, Fix-on-Phone Rate,
Escalation Rate, Work Order Generation Rate, Diagnostic Action Success Rate, and others).
Callers ask for metrics and dimensions *by name*; the layer validates the request and compiles SQL.
No caller-supplied SQL text is ever executed, names are checked against the catalogue, and filter
values are bound as parameters, so execution is read-only by construction.

### Layer 3: ontology in relational tables

- **Schema** (definitions): `ontology_object_type`, `ontology_relationship_type`,
  `ontology_property_def`. Relationship types declare allowed source and target types, and whether
  they are hierarchical.
- **Instances**: `KG_NODE` (`node_id, node_type, canonical_name, definition, synonyms, properties,
  source, ontology_version`) and `KG_EDGE` (`edge_id, source_node_id, target_node_id,
  relationship_type, properties, source, confidence, ontology_version`).
- **Operational object types** (ServiceCase, WorkOrder, DiagnosticExecution) are declared in the
  schema with the table that backs them; their rows stay in the operational tables.

Ingestion (`ontolab/ontology/ingest.py`) loads a JSON or YAML definition and rejects it unless every
node and edge satisfies the schema: known types, allowed endpoints, declared properties of the right
type, no dangling or duplicate edges, acyclic hierarchies. The sample definition is
`data/ontology/laptop_support.yaml`, generated by `python -m ontolab.sample.laptop_support`.

Traversal is recursive SQL (`WITH RECURSIVE`) over `KG_EDGE`: breadth-first, bounded by a depth
limit, cycle-safe (depth bound plus `UNION` de-duplication, each node kept at its minimum depth),
deterministic (ordered tie-breaks), with result limits. Every graph result returns node ids, edge
ids, relationship types and the path taken.

**Rules are evaluated separately from retrieval.** A `GOVERNED_BY` edge says a rule applies; only
`ontolab/ontology/rules.py` says whether it is satisfied, in three values: true, false, or
"cannot decide, these facts are missing". Expressions are parsed into a small safe language (no
calls, no attribute access).

### Layer 4: ontology access layer

22 registered tools. Each has a name, a description, a JSON input schema, an output schema, an
executable function and a declared **capability** (which object types it can find or needs,
which relationships it reads, which operations it provides, runtime or precomputed access).

| Group | Tools |
|---|---|
| Semantic | `query_semantic_view`, `describe_semantic_view` |
| Domain-specific | `resolve_symptom`, `get_symptom_hierarchy`, `get_diagnostic_checklist`, `get_diagnostic_actions`, `get_possible_outcomes`, `get_next_action`, `get_action_constraints`, `get_related_knowledge`, `get_driver_performance` |
| Generic | `get_schema`, `search_objects`, `get_object`, `get_relationship_types`, `get_neighbors`, `traverse_graph`, `find_path`, `query_objects`, `evaluate_rule` |
| Retrieval | `search_ontology_profiles`, `resolve_terminology` |

The registry validates every call (tool name, argument schema), runs it with a timeout, and returns
either data or a structured error (`code`, `message`, `details`), never an exception. It records
arguments, result, duration, errors and ontology version for every invocation.

### The five agent configurations

Same data, same base prompt, same model settings; only the tools differ.

| | Configuration | Tools |
|---|---|---|
| **A** | Semantic View Baseline | `query_semantic_view` |
| **B** | Domain-specific Ontology Tools | semantic + 9 domain tools (run-time traversal; the article's knowledge-graph pattern) |
| **C** | Generic Ontology Tools | semantic + 9 generic tools (the agent discovers the schema; an extension beyond the article) |
| **D** | Flattened GraphRAG | `search_ontology_profiles` + semantic (no run-time traversal) |
| **E** | GraphRAG + Terminology Mappings | D, plus curated mappings in the prompt |

The runtime sends the question and tool definitions to the model, validates and executes each
structured tool call, returns results, and repeats until the model answers, the tool-call budget is
spent, or the time limit is reached. **There is no routing from questions to tools anywhere in the
code**: the model chooses.

### Conversation memory

Outside the benchmark, an agent remembers its conversation, so you can follow up or answer a
clarifying question (`ontolab/agents/memory.py`):

- A conversation belongs to one configuration and has an id. `POST /agents/run` starts one and
  returns `conversation.conversation_id`; send it back to continue.
- A **turn** is everything one question produced: the question, the model's tool calls, the tool
  results and the answer. Turns are stored whole, so a remembered tool call always has its result.
- On the next question the remembered turns are replayed to the model ahead of the new question.
  It can answer from them without calling tools again.
- Memory is a **sliding window** of the most recent `ONTOLAB_MEMORY_TURNS` turns (default 6). Older
  turns are dropped whole and counted; the trace reports how many are remembered and dropped.
- A turn that failed with a model error is not remembered.
- Conversations expire after `ONTOLAB_MEMORY_TTL_S` seconds idle (default one hour). Memory lives in
  the backend process only: nothing is written to disk, and a restart clears it.
- Send `"remember": false` for a stateless request, or `DELETE /agents/conversations/{id}` to forget.

**Benchmark runs never use memory.** Every benchmark question starts fresh, so results are
comparable and one question cannot leak into the next. There is no long-term memory across
conversations and no summarisation of dropped turns.

**Flattened GraphRAG.** One profile per ontology node, precomputed: name, definition, synonyms,
parents and children, all descendants, direct neighbours, attributes aggregated across descendants,
values inherited from ancestors, and multi-hop "related knowledge" chains named by the ontology's
`profile_recipes`, with the node and edge ids each profile was built from. Retrieval is hybrid:
BM25 plus vector similarity, combined by reciprocal rank fusion. Profiles are versioned against a
hash of the graph; when the ontology changes they are reported **stale** until rebuilt.

**Terminology mappings** live in `data/terminology_mappings.yaml`, versioned separately from the
ontology, in three kinds: authoritative, ambiguous (ask the user), and collection (a business
grouping).

## Evaluation

`data/benchmark/questions.yaml`: 46 questions (32 test, 14 dev) across 12 categories and 4
complexity levels. Tune prompts and descriptions on `dev`; report on `test`.

**Dimension 1, tool coverage (no model).** Each task states the object types, relationships,
operations and data it needs, and lists every tool sequence known to work. Coverage is computed from
tool capabilities and the ontology schema by a reachability analysis: a tool that needs an id is
usable only if another tool can produce that id. Access is `direct` (one call), `multi-call`, or
none. Reported: task coverage rate, object / relationship / operation coverage, unsupported tasks.
Coverage is about reach, not tool count: two profile tools cover more tasks than ten domain tools.

**Dimension 2, tool-calling accuracy (covered tasks only).** Tool selection accuracy, required-call
recall, unnecessary calls, argument accuracy, sequencing correctness, multi-step orchestration
success, grounded interpretation accuracy. Wrong tool and wrong arguments are separate findings. A
correct, grounded answer reached by a route the benchmark did not list counts as a valid route.

**Dimension 3, end to end.** Answer score 0/1/2 (as in the article), success rate, completion,
groundedness, hallucination rate, tool calls, tokens, latency, run-to-run consistency.

`Task success = coverage × correct tool use given coverage × successful interpretation` is reported
as a conditional breakdown of the observed runs, not as a claim that the factors are independent.

**Failure attribution.** Every failed run gets a primary cause and contributing causes from: missing
ontology knowledge, missing ontology relationship, insufficient tool coverage, incorrect tool
selection, incorrect tool arguments, incorrect tool sequence, backend execution error, retrieval
failure, incorrect result interpretation, ungrounded final answer. Each maps to a layer: ontology,
access layer, or model.

### How answers are scored, and the limits of that

Scoring is deterministic text matching, with no LLM judge:

- Expected answers are ontology ids (matched by id, canonical name or synonym), phrases, or
  **live reference queries**: numeric expectations are computed from the database at evaluation
  time, matched with tolerance for rounding and percent form.
- Naming many extra objects of the same kind caps the score at 1.
- Groundedness checks that objects and figures in the answer appear in the tool results of that run.

Known limits: it cannot detect negation ("eligible" matches inside "not eligible"), paraphrases that
avoid every name and synonym are missed, and derived figures (a difference between two rates) are
not treated as hallucinations. Read single verdicts as indicative and open the trace when it matters;
the dashboard makes every trace inspectable.

## The web interface

Five sections, plus experiment controls in the sidebar that apply everywhere.

- **Ontology Explorer**: interactive graph (filter by object and relationship type, focus on a
  node and its neighbourhood), node properties, hierarchy, relationship details with source and
  version, the schema, the semantic view, the terminology mappings, and each node's flattened profile.
- **Tool Registry**: tools by configuration, descriptions, schemas, capabilities, enable/disable,
  edit a description, run a tool by hand.
- **Agent Playground**: ask a question of several configurations side by side, then follow up with
  each one (each remembers its own conversation); final answer, tool trace with arguments and
  results, generated SQL, traversed subgraph, metrics, and what is currently in memory.
- **Coverage Explorer**: what each configuration can reach, direct versus multi-call, per task
  with reasons; disable a tool and coverage updates at once.
- **Evaluation Dashboard**: run the benchmark, then coverage versus tool-calling accuracy, success
  by configuration, by tool count, by complexity and category, latency versus success, failure
  attribution, consistency, and every individual result with its trace.

### Controlled experiments

| Experiment | How |
|---|---|
| Add or remove tools, ontology unchanged | Sidebar "Disabled tools" (`disabled_tools` / `extra_tools` in the API) |
| Remove ontology relationships, tools unchanged | Sidebar "Hide relationship types" (`POST /ontology/experiment/disable`) |
| Change tool descriptions, code unchanged | Tool Registry, "Use this description in experiments" |
| Domain-specific vs generic tools | Configurations B vs C |
| Run-time traversal vs precomputed retrieval | Configurations B/C vs D/E |
| Change the model, registry fixed | Sidebar "Model for requests" |
| Traversal depth and retrieval limits | `Limits` in `ontolab/config.py`; tool-call limit in the sidebar |
| Rebuild profiles after an ontology change | Sidebar "Rebuild profiles" (they show as stale until then) |

## Engineering decisions

Made where the brief left room, and worth knowing before trusting a result:

- **JavaScript frontend instead of Streamlit**, at the project owner's request. Plain ES modules
  and SVG, no build step and no CDN, served by FastAPI.
- **DuckDB**, single file, no server. One connection guarded by a lock.
- **The semantic tool takes a structured query, not SQL text.** The model picks metrics, dimensions
  and filters; the layer writes the SQL. This is safer than validating free SQL and mirrors how a
  semantic view is queried, but it means agents cannot express arbitrary SQL.
- **Agent E gets its mappings in the prompt**, as in the article, so D and E have the same tools and
  differ only by the mappings. A `resolve_terminology` tool is also registered for experiments.
- **Local hashing embedder by default** (hashed words and character trigrams with IDF). It needs no
  download or key and is deterministic, but it is a lexical approximation of semantic similarity.
  Set `EMBEDDING_MODEL` to use a real embedding model.
- **Capabilities are declared, not inferred.** Coverage is only as right as those declarations. When
  an agent answers correctly on a task declared uncovered, the run is counted as a success and
  flagged "covered in practice", so over-conservative declarations show up in the results.
- **Operational relationships are not traversed in the graph.** They are declared in the schema and
  reached through `query_objects` and the semantic view.
- **Mock model.** Picks tools by word overlap and echoes results. It exists to test the plumbing.
- **Sources.** The Snowflake post blocks automated readers, so this implementation follows its
  published summary and secondary write-ups: four agent setups (semantic view, + knowledge graph,
  + flattened GraphRAG, + term mappings), `KG_NODE` / `KG_EDGE` tables walked with recursive CTEs,
  22 questions run five times, scored out of 2. Configuration C and the three-dimension evaluation
  are this project's additions.

## Repository layout

```
ontolab/            the Python package (see Architecture)
web/                the JavaScript interface
data/
  ontology/laptop_support.yaml     sample ontology (generated; synthetic)
  semantic_view.yaml               semantic layer definition
  terminology_mappings.yaml        curated mappings for agent E
  benchmark/questions.yaml         46 benchmark tasks with expected results
tests/              unit and end-to-end tests (pytest)
runs/               created at run time: database, evaluation results (git-ignored)
```

To use your own domain, write an ontology definition in the same format, point `ONTOLAB_ONTOLOGY`
at it, and run `python -m ontolab.cli bootstrap --rebuild`. The generic tools, graph store, profile
builder, coverage analysis and interface work unchanged; the domain tools, the operational data
generator, the semantic view and the benchmark are specific to the sample and would need
counterparts.
