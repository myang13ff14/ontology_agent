"""The structured ontology definition format (JSON or YAML) and its validation.

A definition file has two halves that are stored separately:
  * schema    - object types, relationship types, property definitions, constraints
  * instances - nodes and edges that must conform to that schema
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

PropertyType = Literal["string", "number", "boolean", "list", "object"]
_PY_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,), "number": (int, float), "boolean": (bool,), "list": (list,), "object": (dict,),
}


class OntologyValidationError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems[:20]) + (f" (+{len(problems) - 20} more)" if len(problems) > 20 else ""))
        self.problems = problems


class PropertyDef(BaseModel):
    name: str
    type: PropertyType = "string"
    required: bool = False
    description: str = ""


class ObjectType(BaseModel):
    name: str
    description: str = ""
    # "graph": instances are KG nodes. "operational": instances are rows of a relational table.
    storage: Literal["graph", "operational"] = "graph"
    table: str | None = None
    key_column: str | None = None
    name_column: str | None = None
    properties: list[PropertyDef] = Field(default_factory=list)

    @model_validator(mode="after")
    def _operational_needs_table(self):
        if self.storage == "operational" and not (self.table and self.key_column):
            raise ValueError(f"object type {self.name}: operational storage needs table and key_column")
        return self


class RelationshipType(BaseModel):
    name: str
    description: str = ""
    source_types: list[str]
    target_types: list[str]
    hierarchical: bool = False  # child -> parent; must be acyclic
    storage: Literal["graph", "operational"] = "graph"
    table: str | None = None
    source_column: str | None = None
    target_column: str | None = None
    properties: list[PropertyDef] = Field(default_factory=list)


class Node(BaseModel):
    id: str
    type: str
    name: str
    definition: str = ""
    synonyms: list[str] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)
    source: str | None = None


class Edge(BaseModel):
    source: str
    type: str
    target: str
    properties: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 1.0
    provenance: str | None = None


class OntologyInfo(BaseModel):
    name: str
    version: str
    description: str = ""
    source: str = ""
    synthetic: bool = False


class OntologyDefinition(BaseModel):
    ontology: OntologyInfo
    object_types: list[ObjectType]
    relationship_types: list[RelationshipType]
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    # Optional hints for the flattened-profile builder: object type -> list of hop paths.
    profile_recipes: dict[str, list[list[str]]] = Field(default_factory=dict)

    def validate_constraints(self) -> None:
        """Check instances against the schema. Raises OntologyValidationError."""
        problems: list[str] = []
        types = {t.name: t for t in self.object_types}
        rels = {r.name: r for r in self.relationship_types}
        if len(types) != len(self.object_types):
            problems.append("duplicate object type names")
        if len(rels) != len(self.relationship_types):
            problems.append("duplicate relationship type names")
        for rel in self.relationship_types:
            for name in rel.source_types + rel.target_types:
                if name not in types:
                    problems.append(f"relationship type {rel.name}: unknown object type '{name}'")

        nodes: dict[str, Node] = {}
        for node in self.nodes:
            if node.id in nodes:
                problems.append(f"duplicate node id '{node.id}'")
            nodes[node.id] = node
            object_type = types.get(node.type)
            if object_type is None:
                problems.append(f"node {node.id}: unknown object type '{node.type}'")
                continue
            if object_type.storage != "graph":
                problems.append(f"node {node.id}: type '{node.type}' is operational, not a graph type")
            problems += _check_properties(f"node {node.id}", node.properties, object_type.properties)

        seen: set[tuple[str, str, str, str]] = set()
        for edge in self.edges:
            where = f"edge {edge.source} -{edge.type}-> {edge.target}"
            rel = rels.get(edge.type)
            if rel is None:
                problems.append(f"{where}: unknown relationship type")
                continue
            if rel.storage != "graph":
                problems.append(f"{where}: relationship type is operational, not stored in the graph")
            source, target = nodes.get(edge.source), nodes.get(edge.target)
            if source is None or target is None:
                problems.append(f"{where}: dangling endpoint")
                continue
            if source.type not in rel.source_types:
                problems.append(f"{where}: source type {source.type} not allowed (allowed: {rel.source_types})")
            if target.type not in rel.target_types:
                problems.append(f"{where}: target type {target.type} not allowed (allowed: {rel.target_types})")
            if not 0.0 <= edge.confidence <= 1.0:
                problems.append(f"{where}: confidence must be between 0 and 1")
            key = (edge.source, edge.type, edge.target, json.dumps(edge.properties, sort_keys=True))
            if key in seen:
                problems.append(f"{where}: duplicate edge")
            seen.add(key)
            problems += _check_properties(where, edge.properties, rel.properties)

        for rel in self.relationship_types:
            if rel.hierarchical:
                cycle = _find_cycle([(e.source, e.target) for e in self.edges if e.type == rel.name])
                if cycle:
                    problems.append(f"hierarchical relationship {rel.name} has a cycle: {' -> '.join(cycle)}")

        for type_name, paths in self.profile_recipes.items():
            if type_name not in types:
                problems.append(f"profile recipe: unknown object type '{type_name}'")
            for path in paths:
                for hop in path:
                    if hop.lstrip("~") not in rels:
                        problems.append(f"profile recipe {type_name}: unknown relationship '{hop}'")
        if problems:
            raise OntologyValidationError(problems)


def _check_properties(where: str, values: dict[str, Any], defs: list[PropertyDef]) -> list[str]:
    problems = []
    known = {d.name: d for d in defs}
    for d in defs:
        if d.required and values.get(d.name) is None:
            problems.append(f"{where}: missing required property '{d.name}'")
    for name, value in values.items():
        d = known.get(name)
        if d is None:
            problems.append(f"{where}: property '{name}' is not defined in the schema")
        elif value is not None and not _matches(value, d.type):
            problems.append(f"{where}: property '{name}' should be {d.type}, got {type(value).__name__}")
    return problems


def _matches(value: Any, type_name: str) -> bool:
    if type_name == "number" and isinstance(value, bool):
        return False
    return isinstance(value, _PY_TYPES[type_name])


def _find_cycle(pairs: list[tuple[str, str]]) -> list[str] | None:
    graph: dict[str, list[str]] = {}
    for child, parent in pairs:
        graph.setdefault(child, []).append(parent)
    state: dict[str, int] = {}

    def visit(node: str, trail: list[str]) -> list[str] | None:
        state[node] = 1
        for nxt in sorted(graph.get(node, [])):
            if state.get(nxt) == 1:
                return trail + [node, nxt]
            if state.get(nxt) is None:
                found = visit(nxt, trail + [node])
                if found:
                    return found
        state[node] = 2
        return None

    for start in sorted(graph):
        if state.get(start) is None:
            found = visit(start, [])
            if found:
                return found
    return None


def load_definition(path: str | Path) -> OntologyDefinition:
    """Read a .yaml/.yml/.json ontology definition and validate it."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    raw = json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
    definition = OntologyDefinition.model_validate(raw)
    definition.validate_constraints()
    return definition
