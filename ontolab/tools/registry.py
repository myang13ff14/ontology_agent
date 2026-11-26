"""Tool registry: definitions, validation, execution and logging.

A tool is a named, described, schema-validated function. The registry is the only
way agents (and the manual-execution UI) reach the ontology, semantic layer and
profile index. It does not decide which tool answers which question: the LLM
chooses, the registry validates and executes.
"""

from __future__ import annotations

import concurrent.futures
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from ..errors import ToolError

# Operations a tool can make available. Used by coverage analysis.
OPERATIONS = [
    "resolve_term", "object_details", "hierarchy_expansion", "neighbor_lookup", "multi_hop_traversal",
    "path_finding", "filter_objects", "schema_discovery", "conditional_branching", "rule_evaluation",
    "metric_aggregation", "profile_retrieval", "terminology_mapping",
]

# Wildcards resolved against the live ontology schema at analysis time.
ALL_GRAPH = "*graph"          # every graph-stored object type / relationship type
ALL_OPERATIONAL = "*operational"  # every operational object type / relationship type
OPERATIONAL_TARGETS = "*operational_targets"  # graph types that operational relationships point at


@dataclass(frozen=True)
class Capability:
    """What part of the ontology a tool exposes.

    entry_objects   object types the tool can find from free text or with no id (a starting point)
    input_objects   object types whose id the tool needs as input
    output_objects  object types the tool returns
    relationships   relationship types the tool reads
    operations      entries of OPERATIONS
    access          "runtime" (queries the graph when called) or "precomputed" (reads stored profiles)
    """

    entry_objects: tuple[str, ...] = ()
    input_objects: tuple[str, ...] = ()
    output_objects: tuple[str, ...] = ()
    relationships: tuple[str, ...] = ()
    operations: tuple[str, ...] = ()
    access: str = "runtime"

    def to_dict(self) -> dict:
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in self.__dict__.items()}


@dataclass
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    output_schema: dict
    fn: Callable[[BaseModel], dict]
    capability: Capability
    category: str  # semantic | domain | generic | graphrag | terminology

    def input_schema(self) -> dict:
        return _clean_schema(self.input_model.model_json_schema())


@dataclass
class ToolInvocation:
    tool: str
    arguments: Any
    ok: bool
    result: Any = None
    error: dict | None = None
    duration_ms: float = 0.0
    ontology_version: str | None = None
    provenance: dict = field(default_factory=dict)
    generated_sql: str | None = None

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    def payload(self) -> dict:
        """What the LLM is shown."""
        return self.result if self.ok else {"error": self.error}


def _clean_schema(schema: dict) -> dict:
    """Inline $defs and drop pydantic titles so every provider accepts the schema."""
    defs = schema.pop("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(dict(defs[node["$ref"].split("/")[-1]]))
            return {k: walk(v) for k, v in node.items() if k != "title"}
        if isinstance(node, list):
            return [walk(item) for item in node]
        return node

    return walk(schema)


class ToolRegistry:
    def __init__(self, version_fn: Callable[[], str], timeout_s: float = 10.0):
        self._tools: dict[str, ToolSpec] = {}
        self._version_fn = version_fn
        self.timeout_s = timeout_s
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="tool")

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"tool '{spec.name}' is already registered")
        unknown = [op for op in spec.capability.operations if op not in OPERATIONS]
        if unknown:
            raise ValueError(f"tool '{spec.name}': unknown operations {unknown}")
        self._tools[spec.name] = spec

    def names(self) -> list[str]:
        return list(self._tools)

    def get(self, name: str) -> ToolSpec:
        return self._tools[name]

    def has(self, name: str) -> bool:
        return name in self._tools

    def describe(self, name: str, description_overrides: dict[str, str] | None = None) -> dict:
        spec = self._tools[name]
        return {
            "name": spec.name, "category": spec.category,
            "description": (description_overrides or {}).get(name) or spec.description,
            "default_description": spec.description,
            "input_schema": spec.input_schema(), "output_schema": spec.output_schema,
            "capabilities": spec.capability.to_dict(),
        }

    def llm_tools(self, names: list[str], description_overrides: dict[str, str] | None = None) -> list[dict]:
        """Tool definitions in the neutral {name, description, input_schema} form."""
        return [{"name": n, "description": (description_overrides or {}).get(n) or self._tools[n].description,
                 "input_schema": self._tools[n].input_schema()} for n in names]

    def execute(self, name: str, arguments: Any, allowed: list[str] | None = None,
                timeout_s: float | None = None) -> ToolInvocation:
        """Validate and run one tool call. Never raises: failures come back as structured errors."""
        started = time.perf_counter()
        version = self._safe_version()

        def failed(code: str, message: str, **details: Any) -> ToolInvocation:
            return ToolInvocation(tool=name, arguments=arguments, ok=False, ontology_version=version,
                                  error={"code": code, "message": message, "details": details},
                                  duration_ms=round((time.perf_counter() - started) * 1000, 2))

        if name not in self._tools or (allowed is not None and name not in allowed):
            return failed("unknown_tool", f"Tool '{name}' is not available.",
                          available=sorted(allowed if allowed is not None else self._tools))
        spec = self._tools[name]
        if not isinstance(arguments, dict):
            return failed("invalid_arguments", "Tool arguments must be a JSON object.")
        try:
            parsed = spec.input_model.model_validate(arguments)
        except ValidationError as exc:
            problems = [{"field": ".".join(str(p) for p in e["loc"]), "problem": e["msg"]} for e in exc.errors()]
            return failed("invalid_arguments", f"Arguments for {name} failed validation.", problems=problems)

        future = self._pool.submit(spec.fn, parsed)
        try:
            result = future.result(timeout=timeout_s or self.timeout_s)
        except concurrent.futures.TimeoutError:
            future.cancel()
            return failed("timeout", f"{name} did not finish within {timeout_s or self.timeout_s:g} seconds.")
        except ToolError as exc:
            return failed(exc.code, exc.message, **exc.details)
        except Exception as exc:  # a bug or infrastructure fault in the backend function
            return failed("backend_error", f"{type(exc).__name__}: {exc}")

        try:
            json.dumps(result, default=str)
        except (TypeError, ValueError) as exc:
            return failed("backend_error", f"Tool returned a non-serialisable result: {exc}")
        provenance = result.get("provenance", {}) if isinstance(result, dict) else {}
        return ToolInvocation(
            tool=name, arguments=arguments, ok=True, result=result, ontology_version=version,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
            provenance={"node_ids": list(provenance.get("node_ids", [])), "edge_ids": list(provenance.get("edge_ids", []))},
            generated_sql=result.get("sql") if isinstance(result, dict) else None,
        )

    def _safe_version(self) -> str | None:
        try:
            return self._version_fn()
        except Exception:
            return None
