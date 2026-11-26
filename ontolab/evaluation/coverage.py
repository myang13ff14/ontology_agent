"""Tool coverage: can the registered tools, taken together, do what a task needs?

This is computed from tool capability declarations and the ontology schema. It
never runs an LLM, so it separates "the tools cannot do this" from "the model
failed to use the tools". Coverage is about reach, not about how many tools exist.

Access levels:
  direct      reachable with one tool call
  multi_call  reachable only by chaining calls (an earlier call supplies the id)
  none        no enabled tool reaches it
"""

from __future__ import annotations

from ..ontology.store import OntologyStore
from ..semantic.view import SemanticView
from ..tools.registry import ALL_GRAPH, ALL_OPERATIONAL, OPERATIONAL_TARGETS, OPERATIONS, ToolRegistry
from .benchmark import Task


class CoverageAnalyzer:
    def __init__(self, registry: ToolRegistry, store: OntologyStore, semantic: SemanticView):
        self.registry = registry
        object_types, relationship_types = store.object_types(), store.relationship_types()
        self.graph_types = sorted(n for n, t in object_types.items() if t["storage"] == "graph")
        self.operational_types = sorted(n for n, t in object_types.items() if t["storage"] == "operational")
        self.graph_rels = sorted(n for n, r in relationship_types.items() if r["storage"] == "graph")
        self.operational_rels = sorted(n for n, r in relationship_types.items() if r["storage"] == "operational")
        self.operational_targets = sorted({t for n in self.operational_rels for t in relationship_types[n]["target_types"]
                                           if t in self.graph_types})
        self.known_data = set(semantic.metrics)
        for table in semantic.tables.values():
            self.known_data |= set(table["dimensions"]) | set(table.get("facts", {}))
        for t in object_types.values():
            self.known_data |= {p["name"] for p in t["properties"]}

    def _expand(self, items: tuple[str, ...], relationships: bool = False) -> set[str]:
        out: set[str] = set()
        for item in items:
            if item == ALL_GRAPH:
                out |= set(self.graph_rels if relationships else self.graph_types)
            elif item == ALL_OPERATIONAL:
                out |= set(self.operational_rels if relationships else self.operational_types)
            elif item == OPERATIONAL_TARGETS:
                out |= set(self.operational_targets)
            else:
                out.add(item)
        return out

    def access(self, enabled_tools: list[str], prompt_operations: tuple[str, ...] = ()) -> dict:
        """Reach of a tool set over object types, relationship types and operations."""
        caps = {name: self.registry.get(name).capability for name in enabled_tools if self.registry.has(name)}
        object_calls: dict[str, int] = {}
        object_via: dict[str, set[str]] = {}

        def offer(object_type: str, calls: int, tool: str) -> bool:
            if calls < object_calls.get(object_type, 99):
                object_calls[object_type] = calls
                object_via[object_type] = {tool}
                return True
            if calls == object_calls.get(object_type):
                object_via[object_type].add(tool)
            return False

        tool_calls: dict[str, int] = {}
        changed = True
        while changed:  # fixed point: an output of one tool can be the input of another
            changed = False
            for name, cap in caps.items():
                inputs = self._expand(cap.input_objects)
                if cap.entry_objects or not inputs:
                    needed = 1
                else:
                    reachable = [object_calls[t] for t in inputs if t in object_calls]
                    if not reachable:
                        continue
                    needed = min(reachable) + 1
                if needed < tool_calls.get(name, 99):
                    tool_calls[name] = needed
                    changed = True
                for object_type in self._expand(cap.entry_objects):
                    changed |= offer(object_type, 1, name)
                for object_type in self._expand(cap.output_objects):
                    changed |= offer(object_type, tool_calls[name], name)

        def level(calls: int | None) -> str:
            return "none" if calls is None else ("direct" if calls == 1 else "multi_call")

        objects = {t: {"access": level(object_calls.get(t)), "calls": object_calls.get(t),
                       "via": sorted(object_via.get(t, []))} for t in self.graph_types + self.operational_types}
        relationships = {}
        for rel in self.graph_rels + self.operational_rels:
            users = {n: tool_calls[n] for n, cap in caps.items()
                     if n in tool_calls and rel in self._expand(cap.relationships, relationships=True)}
            best = min(users.values()) if users else None
            relationships[rel] = {"access": level(best), "calls": best,
                                  "via": sorted(n for n, c in users.items() if c == best),
                                  "precomputed_only": bool(users) and all(caps[n].access == "precomputed" for n in users)}
        operations = {}
        for op in OPERATIONS:
            users = {n: tool_calls[n] for n, cap in caps.items() if n in tool_calls and op in cap.operations}
            best = min(users.values()) if users else None
            via = sorted(n for n, c in users.items() if c == best)
            if op in prompt_operations:
                best, via = 0, ["(prompt)"]
            operations[op] = {"access": "direct" if best in (0, 1) else level(best), "calls": best, "via": via}
        return {
            "enabled_tools": [n for n in enabled_tools if n in caps], "objects": objects,
            "relationships": relationships, "operations": operations,
            "unusable_tools": sorted(n for n in caps if n not in tool_calls),
        }

    def task_coverage(self, task: Task, enabled_tools: list[str], prompt_operations: tuple[str, ...] = (),
                      access: dict | None = None) -> dict:
        access = access or self.access(enabled_tools, prompt_operations)
        req = task.required

        def split(needed: list[str], table: dict) -> tuple[list[str], list[str]]:
            have = [n for n in needed if table.get(n, {}).get("access", "none") != "none"]
            return have, [n for n in needed if n not in have]

        objects_ok, objects_missing = split(req.objects, access["objects"])
        rels_ok, rels_missing = split(req.relationships, access["relationships"])
        ops_ok, ops_missing = split(req.operations, access["operations"])
        data_missing = [d for d in req.data if d not in self.known_data]
        enabled = set(access["enabled_tools"])
        usable = [s for s in task.acceptable_sequences if set(s) <= enabled]
        capability_ok = not (objects_missing or rels_missing or ops_missing or data_missing)
        covered = capability_ok and bool(usable)
        reasons = []
        if objects_missing:
            reasons.append(f"object types not reachable: {objects_missing}")
        if rels_missing:
            reasons.append(f"relationships not readable: {rels_missing}")
        if ops_missing:
            reasons.append(f"operations not available: {ops_missing}")
        if data_missing:
            reasons.append(f"data not present: {data_missing}")
        if not usable:
            reasons.append("no acceptable tool sequence is fully enabled")
        min_calls = min((len(s) for s in usable), default=None)

        def rate(ok: list, needed: list) -> float:
            return 1.0 if not needed else len(ok) / len(needed)

        return {
            "task_id": task.id, "covered": covered, "capability_ok": capability_ok, "sequence_ok": bool(usable),
            "object_coverage": rate(objects_ok, req.objects), "relationship_coverage": rate(rels_ok, req.relationships),
            "operation_coverage": rate(ops_ok, req.operations),
            "missing": {"objects": objects_missing, "relationships": rels_missing, "operations": ops_missing,
                        "data": data_missing},
            "usable_sequences": usable, "min_calls": min_calls,
            "access": None if not covered else ("direct" if min_calls == 1 else "multi_call"),
            "reasons": reasons,
        }

    def report(self, tasks: list[Task], enabled_tools: list[str], prompt_operations: tuple[str, ...] = ()) -> dict:
        access = self.access(enabled_tools, prompt_operations)
        rows = [self.task_coverage(t, enabled_tools, prompt_operations, access) for t in tasks]

        def mean(key: str) -> float:
            return round(sum(r[key] for r in rows) / len(rows), 4) if rows else 0.0

        return {
            "tool_count": len(access["enabled_tools"]),
            "task_coverage_rate": round(sum(r["covered"] for r in rows) / len(rows), 4) if rows else 0.0,
            "object_access_coverage": mean("object_coverage"),
            "relationship_access_coverage": mean("relationship_coverage"),
            "operation_coverage": mean("operation_coverage"),
            "direct_tasks": sum(1 for r in rows if r["access"] == "direct"),
            "multi_call_tasks": sum(1 for r in rows if r["access"] == "multi_call"),
            "unsupported_tasks": [{"task_id": r["task_id"], "reasons": r["reasons"]} for r in rows if not r["covered"]],
            "tasks": rows, "access": access,
        }
