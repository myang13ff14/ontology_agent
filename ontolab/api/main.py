"""FastAPI backend. It also serves the JavaScript frontend in web/ at "/".

    uvicorn ontolab.api.main:app --port 8600        then open http://localhost:8600

Experiment controls (disabled tools, description overrides, call limits, LLM choice)
are request parameters, so nothing about one experiment leaks into another. The
one piece of shared state is the ontology experiment switch (hidden relationship
types), which is explicit and reported in every status and trace.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..agents.configs import CONFIGS, system_prompt
from ..config import ROOT
from ..agents.runtime import AgentRuntime
from ..errors import ToolError
from ..evaluation.runner import Evaluation
from ..llm.base import LLMError
from ..llm.factory import PROVIDERS, describe, make_llm
from ..ontology.ingest import ingest
from ..semantic.view import SemanticQuery
from ..services import Services, build_services

state: dict[str, Any] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    if "services" not in state:  # tests inject their own
        install(build_services())
    yield


def install(services: Services, llm=None) -> None:
    state["services"] = services
    state["runtime"] = AgentRuntime(services, llm)
    state["evaluation"] = Evaluation(services, state["runtime"])


app = FastAPI(title="Ontology-grounded agent lab", version="1.0.0", lifespan=lifespan)


def svc() -> Services:
    return state["services"]


def runtime() -> AgentRuntime:
    return state["runtime"]


def evaluation() -> Evaluation:
    return state["evaluation"]


def pick_llm(provider: str | None, model: str | None):
    """Per-request LLM override; default is the server's configured LLM."""
    if not provider and not model:
        return None
    try:
        return make_llm(provider, model)
    except LLMError as exc:
        raise HTTPException(400, str(exc)) from None


class LLMChoice(BaseModel):
    llm_provider: str | None = Field(default=None, description=f"One of {PROVIDERS}; omit for the server default.")
    llm_model: str | None = None


class RunRequest(LLMChoice):
    question: str
    config: str = "B"
    disabled_tools: list[str] = Field(default_factory=list)
    extra_tools: list[str] = Field(default_factory=list)
    description_overrides: dict[str, str] = Field(default_factory=dict)
    max_tool_calls: int | None = Field(default=None, ge=0, le=40)
    tool_timeout_s: float | None = Field(default=None, gt=0, le=120)


class ExecuteRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)


class CoverageRequest(BaseModel):
    configs: list[str] | None = None
    split: str = "all"
    disabled_tools: list[str] = Field(default_factory=list)
    extra_tools: dict[str, list[str]] = Field(default_factory=dict)


class EvaluationRequest(LLMChoice):
    configs: list[str] | None = None
    split: str = "test"
    repeats: int = Field(default=1, ge=1, le=10)
    task_ids: list[str] | None = None
    limit: int | None = Field(default=None, ge=1)
    disabled_tools: list[str] = Field(default_factory=list)
    description_overrides: dict[str, str] = Field(default_factory=dict)
    max_tool_calls: int | None = Field(default=None, ge=0, le=40)
    concurrency: int = Field(default=4, ge=1, le=16)
    label: str = ""


class DisableRequest(BaseModel):
    relationship_types: list[str] = Field(default_factory=list)
    edges: list[str] = Field(default_factory=list)


def check_configs(configs: list[str] | None) -> None:
    unknown = [c for c in configs or [] if c not in CONFIGS]
    if unknown:
        raise HTTPException(400, f"Unknown agent configurations {unknown}. Valid: {sorted(CONFIGS)}")


# ------------------------------------------------------------------ status

@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/status")
def status() -> dict:
    out = svc().status()
    try:
        out["llm"] = describe(runtime().llm())
    except LLMError as exc:
        out["llm"] = {"provider": "unavailable", "model": str(exc), "is_mock": False}
    out["llm_providers"] = list(PROVIDERS)
    out["limits"] = {**svc().settings.limits.__dict__, "max_tool_calls": svc().settings.max_tool_calls,
                     "tool_timeout_s": svc().settings.tool_timeout_s}
    return out


# ---------------------------------------------------------------- ontology

@app.get("/ontology/schema")
def ontology_schema() -> dict:
    schema = svc().store.get_schema()
    schema["relationship_types_including_disabled"] = list(svc().store.relationship_types().values())
    return schema


@app.get("/ontology/nodes")
def ontology_nodes(object_type: str | None = None, query: str | None = None) -> dict:
    store = svc().store
    if query:
        try:
            return {"nodes": store.search_objects(query, object_type, 50)["matches"]}
        except ToolError as exc:
            raise HTTPException(400, exc.message) from None
    return {"nodes": [{k: n[k] for k in ("node_id", "node_type", "canonical_name")} for n in store.all_nodes(object_type)]}


@app.get("/ontology/node/{node_id}")
def ontology_node(node_id: str) -> dict:
    store = svc().store
    try:
        node = store.get_object(node_id)
        return {"node": node, "neighbors": store.get_neighbors(node_id, None, "both", 200)["neighbors"],
                "hierarchy": store.hierarchy(node_id)}
    except ToolError as exc:
        raise HTTPException(404, exc.message) from None


@app.get("/ontology/graph")
def ontology_graph(object_types: str | None = None, relationship_types: str | None = None,
                   focus: str | None = None, depth: int = 1, include_disabled: bool = False) -> dict:
    """Nodes and edges for visualisation, optionally filtered or centred on one node."""
    store = svc().store
    types = set(filter(None, (object_types or "").split(",")))
    rels = set(filter(None, (relationship_types or "").split(",")))
    edges = store.all_edges(include_disabled)
    nodes = {n["node_id"]: n for n in store.all_nodes()}
    keep: set[str] | None = None
    if focus:
        try:
            reached = store.traverse_graph(focus, sorted(rels) or None, max(1, min(depth, 4)), "both", 200)
        except ToolError as exc:
            raise HTTPException(404, exc.message) from None
        keep = set(reached["provenance"]["node_ids"])
    chosen_edges = [e for e in edges
                    if (not rels or e["relationship_type"] in rels)
                    and (keep is None or (e["source_node_id"] in keep and e["target_node_id"] in keep))
                    and (not types or (nodes[e["source_node_id"]]["node_type"] in types
                                       and nodes[e["target_node_id"]]["node_type"] in types))]
    used = {e["source_node_id"] for e in chosen_edges} | {e["target_node_id"] for e in chosen_edges}
    if focus:
        used.add(focus)
    elif types:
        used |= {i for i, n in nodes.items() if n["node_type"] in types}
    return {"nodes": [{k: nodes[i][k] for k in ("node_id", "node_type", "canonical_name")} for i in sorted(used)],
            "edges": [{k: e[k] for k in ("edge_id", "source_node_id", "target_node_id", "relationship_type",
                                         "properties", "confidence", "source", "ontology_version")} for e in chosen_edges]}


@app.post("/ontology/subgraph")
def ontology_subgraph(body: dict) -> dict:
    """Nodes and edges by id: used to draw the subgraph an agent run touched."""
    store = svc().store
    edge_ids = set(body.get("edge_ids") or [])
    edges = [e for e in store.all_edges(True) if e["edge_id"] in edge_ids]
    ids = set(body.get("node_ids") or []) | {e["source_node_id"] for e in edges} | {e["target_node_id"] for e in edges}
    return {"nodes": list(store.get_objects(sorted(ids)).values()),
            "edges": [{k: e[k] for k in ("edge_id", "source_node_id", "target_node_id", "relationship_type", "properties")}
                      for e in edges]}


@app.post("/ontology/experiment/disable")
def ontology_disable(body: DisableRequest) -> dict:
    """Hide relationship types or edges from every tool, without touching tools or data."""
    try:
        disabled = svc().store.set_disabled(body.relationship_types, body.edges)
    except ToolError as exc:
        raise HTTPException(400, exc.message) from None
    return {"disabled": disabled, "ontology_version": svc().store.version(), "profiles": svc().profiles.status(),
            "note": "Flattened profiles are now stale until rebuilt; that is part of the experiment."}


@app.post("/ontology/reload")
def ontology_reload() -> dict:
    """Re-ingest the ontology definition file (after editing it) and clear experiment switches."""
    summary = ingest(svc().db, svc().settings.ontology_path)
    return {"loaded": summary, "profiles": svc().profiles.status()}


# ---------------------------------------------------------------- semantic

@app.get("/semantic/definition")
def semantic_definition() -> dict:
    return svc().semantic.catalog()


@app.post("/semantic/query")
def semantic_query(query: SemanticQuery) -> dict:
    try:
        return svc().semantic.run(query)
    except ToolError as exc:
        raise HTTPException(400, {"code": exc.code, "message": exc.message, "details": exc.details}) from None


# ----------------------------------------------------------------- graphrag

@app.get("/graphrag/status")
def graphrag_status() -> dict:
    return svc().profiles.status()


@app.post("/graphrag/rebuild")
def graphrag_rebuild() -> dict:
    return svc().profiles.rebuild()


@app.get("/graphrag/profile/{node_id}")
def graphrag_profile(node_id: str) -> dict:
    for profile in svc().profiles.profiles:
        if profile["node_id"] == node_id:
            return profile
    raise HTTPException(404, f"No profile for '{node_id}'.")


@app.get("/terminology")
def terminology() -> dict:
    return svc().terminology.catalog()


@app.post("/terminology/reload")
def terminology_reload() -> dict:
    svc().terminology.reload()
    return {"version": svc().terminology.version, "mappings": len(svc().terminology.mappings)}


# -------------------------------------------------------------------- tools

@app.get("/tools")
def tools() -> dict:
    registry = svc().registry
    membership = {name: [k for k, c in CONFIGS.items() if name in c.tools] for name in registry.names()}
    return {"tools": [{**registry.describe(name), "configs": membership[name]} for name in registry.names()]}


@app.post("/tools/{name}/execute")
def tool_execute(name: str, body: ExecuteRequest) -> dict:
    if not svc().registry.has(name):
        raise HTTPException(404, f"Unknown tool '{name}'.")
    return svc().registry.execute(name, body.arguments).to_dict()


# ------------------------------------------------------------------- agents

@app.get("/agents")
def agents() -> dict:
    terminology_text = svc().terminology.render_for_prompt()
    return {"configs": [{**c.to_dict(), "system_prompt": system_prompt(c, terminology_text)} for c in CONFIGS.values()]}


@app.post("/agents/run")
def agents_run(body: RunRequest) -> dict:
    check_configs([body.config])
    if not body.question.strip():
        raise HTTPException(400, "Question is empty.")
    try:
        return runtime().run(body.question.strip(), body.config, llm=pick_llm(body.llm_provider, body.llm_model),
                             disabled_tools=body.disabled_tools, extra_tools=body.extra_tools,
                             description_overrides=body.description_overrides, max_tool_calls=body.max_tool_calls,
                             tool_timeout_s=body.tool_timeout_s)
    except LLMError as exc:
        raise HTTPException(502, str(exc)) from None


# --------------------------------------------------------------- evaluation

@app.get("/benchmark")
def benchmark() -> dict:
    return {"tasks": [t.model_dump() for t in evaluation().tasks]}


@app.post("/coverage")
def coverage(body: CoverageRequest) -> dict:
    check_configs(body.configs)
    return evaluation().coverage(body.configs, None if body.split == "all" else body.split, body.disabled_tools,
                                 body.extra_tools)


@app.post("/evaluation/run")
def evaluation_run(body: EvaluationRequest) -> dict:
    """Start a benchmark run in the background. Poll /evaluation/jobs/{job_id}."""
    check_configs(body.configs)
    try:
        llm = pick_llm(body.llm_provider, body.llm_model) or runtime().llm()
    except LLMError as exc:
        raise HTTPException(400, str(exc)) from None
    job_id = evaluation().start(
        configs=body.configs, split=None if body.split == "all" else body.split, repeats=body.repeats,
        task_ids=body.task_ids, limit=body.limit, llm=llm, disabled_tools=body.disabled_tools,
        description_overrides=body.description_overrides, max_tool_calls=body.max_tool_calls,
        concurrency=body.concurrency, label=body.label)
    return {"job_id": job_id, "llm": describe(llm)}


@app.get("/evaluation/jobs/{job_id}")
def evaluation_job(job_id: str) -> dict:
    job = evaluation().job(job_id)
    if job is None:
        raise HTTPException(404, "Unknown job.")
    return job


@app.get("/evaluation/runs")
def evaluation_runs() -> dict:
    return {"runs": evaluation().list_runs()}


@app.get("/evaluation/runs/{run_id}")
def evaluation_result(run_id: str, include_traces: bool = False) -> dict:
    data = evaluation().load_run(run_id)
    if data is None:
        raise HTTPException(404, "Unknown evaluation run.")
    if not include_traces:
        data = {**data, "results": [{k: v for k, v in r.items() if k != "trace"} for r in data["results"]]}
    return data


@app.get("/evaluation/runs/{run_id}/trace")
def evaluation_trace(run_id: str, task_id: str, config: str, repeat: int = 1) -> dict:
    data = evaluation().load_run(run_id)
    if data is None:
        raise HTTPException(404, "Unknown evaluation run.")
    for result in data["results"]:
        if result["task_id"] == task_id and result["config"] == config and result["repeat"] == repeat:
            return result
    raise HTTPException(404, "No such result in this run.")


# The JavaScript frontend. Mounted last so every API route above takes precedence.
app.mount("/", StaticFiles(directory=str(ROOT / "web"), html=True), name="web")
