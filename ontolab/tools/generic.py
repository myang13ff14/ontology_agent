"""Generic ontology tools. They work from ontology metadata only and contain no
domain-specific logic: point them at a different ontology and they still work."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from ..ontology import rules
from ..ontology.store import OntologyAccessError, OntologyStore
from .registry import ALL_GRAPH, ALL_OPERATIONAL, Capability, ToolRegistry, ToolSpec

Direction = Literal["out", "in", "both"]
PROVENANCE = {"type": "object", "properties": {"node_ids": {"type": "array"}, "edge_ids": {"type": "array"}}}


def _out(**properties: Any) -> dict:
    return {"type": "object", "properties": {**properties, "provenance": PROVENANCE}}


class NoArgs(BaseModel):
    pass


class SearchObjectsArgs(BaseModel):
    query: str = Field(description="Words to look for in names, synonyms and definitions.")
    object_type: str | None = Field(default=None, description="Restrict to one object type from get_schema.")
    limit: int = Field(default=10, ge=1, le=50, description="Maximum matches to return.")


class GetObjectArgs(BaseModel):
    object_id: str = Field(description="Id of the object, e.g. from search_objects.")


class RelationshipTypesArgs(BaseModel):
    object_type: str = Field(description="An object type name from get_schema.")


class NeighborsArgs(BaseModel):
    object_id: str = Field(description="Id of the object to start from.")
    relationship_type: str | None = Field(default=None, description="Only follow this relationship type.")
    direction: Direction = Field(default="both", description="'out' follows edges from the object, 'in' edges pointing at it.")
    limit: int = Field(default=50, ge=1, le=200)


class TraverseArgs(BaseModel):
    start_node_id: str = Field(description="Id of the object to start from.")
    relationship_types: list[str] | None = Field(default=None, description="Only follow these relationship types. Omit for all.")
    max_depth: int = Field(default=2, ge=1, le=10, description="Maximum hops. Capped by the server's depth limit.")
    direction: Direction = Field(default="out", description="Edge direction to follow on every hop.")
    limit: int = Field(default=100, ge=1, le=200, description="Maximum reached objects to return.")


class FindPathArgs(BaseModel):
    source_node_id: str
    target_node_id: str
    relationship_types: list[str] | None = Field(default=None, description="Only use these relationship types.")
    max_depth: int | None = Field(default=None, ge=1, le=10, description="Maximum path length.")


class QueryObjectsArgs(BaseModel):
    object_type: str = Field(description="An object type name from get_schema (graph or operational).")
    filters: dict[str, Any] = Field(default_factory=dict, description="Field -> required value (or list of accepted values). "
                                    "Fields are the type's properties, plus node_id and canonical_name for graph types.")
    limit: int = Field(default=25, ge=1, le=200)


class EvaluateRuleArgs(BaseModel):
    rule_id: str | None = Field(default=None, description="Id of an object that has an 'expression' property.")
    context: dict[str, Any] = Field(default_factory=dict, description="Known facts, e.g. {\"in_warranty\": true}.")
    expression: str | None = Field(default=None, description="Alternatively, a raw condition to evaluate, such as the "
                                   "'condition' property found on an edge.")


def register_generic_tools(registry: ToolRegistry, store: OntologyStore) -> None:
    def evaluate_rule(args: EvaluateRuleArgs) -> dict:
        if bool(args.rule_id) == bool(args.expression):
            raise OntologyAccessError("invalid_argument", "Give exactly one of rule_id or expression.")
        node = None
        expression = args.expression
        if args.rule_id:
            node = store.get_object(args.rule_id)
            expression = node["properties"].get("expression")
            if not expression:
                raise OntologyAccessError("not_a_rule", f"Object '{args.rule_id}' has no 'expression' property.",
                                          object_type=node["node_type"])
        try:
            verdict = rules.evaluate(expression, args.context)
            needs = rules.referenced_names(expression)
        except rules.RuleError as exc:
            raise OntologyAccessError("invalid_expression", str(exc), expression=expression) from None
        return {
            "rule": ({"node_id": node["node_id"], "canonical_name": node["canonical_name"],
                      "properties": node["properties"]} if node else None),
            **verdict, "required_context": needs,
            "interpretation": {True: "satisfied", False: "not satisfied",
                               None: "cannot be decided: context is missing facts"}[verdict["satisfied"]],
            "provenance": {"node_ids": [node["node_id"]] if node else [], "edge_ids": []},
        }

    specs = [
        ToolSpec("get_schema",
                 "Describe the ontology itself: every object type with its properties, and every relationship type with "
                 "the object types it connects. Call this first when you do not know what kinds of things and links exist.",
                 NoArgs, _out(ontology={"type": "object"}, object_types={"type": "array"}, relationship_types={"type": "array"}),
                 lambda a: {**store.get_schema(), "provenance": {"node_ids": [], "edge_ids": []}},
                 Capability(operations=("schema_discovery",)), "generic"),
        ToolSpec("search_objects",
                 "Find ontology objects by name, synonym or definition text. Returns ranked matches with ids. Use it to "
                 "turn a phrase into an object id before calling tools that need ids.",
                 SearchObjectsArgs, _out(matches={"type": "array"}, total_matches={"type": "integer"}),
                 lambda a: store.search_objects(a.query, a.object_type, a.limit),
                 Capability(entry_objects=(ALL_GRAPH,), output_objects=(ALL_GRAPH,), operations=("resolve_term",)), "generic"),
        ToolSpec("get_object",
                 "Fetch one object by id with its definition, synonyms and all properties.",
                 GetObjectArgs, _out(node_id={"type": "string"}, properties={"type": "object"}),
                 lambda a: {**store.get_object(a.object_id), "provenance": {"node_ids": [a.object_id], "edge_ids": []}},
                 Capability(input_objects=(ALL_GRAPH,), output_objects=(ALL_GRAPH,), operations=("object_details",)), "generic"),
        ToolSpec("get_relationship_types",
                 "List the relationship types that can leave or enter one object type, with how many edges exist. Use it "
                 "to decide which relationship to follow from an object.",
                 RelationshipTypesArgs, _out(outgoing={"type": "array"}, incoming={"type": "array"}),
                 lambda a: {**store.get_relationship_types(a.object_type), "provenance": {"node_ids": [], "edge_ids": []}},
                 Capability(operations=("schema_discovery",)), "generic"),
        ToolSpec("get_neighbors",
                 "Objects one hop away from an object, with the connecting edge (id, type, direction, properties). "
                 "Edge properties carry things like ordering or conditions.",
                 NeighborsArgs, _out(object={"type": "object"}, neighbors={"type": "array"}),
                 lambda a: store.get_neighbors(a.object_id, a.relationship_type, a.direction, a.limit),
                 Capability(input_objects=(ALL_GRAPH,), output_objects=(ALL_GRAPH,), relationships=(ALL_GRAPH,),
                            operations=("neighbor_lookup",)), "generic"),
        ToolSpec("traverse_graph",
                 "Everything reachable from an object within max_depth hops along the chosen relationship types and "
                 "direction, each with its depth and the path taken. Use direction 'in' with a hierarchy relationship "
                 "to get all descendants, 'out' to get all ancestors.",
                 TraverseArgs, _out(nodes={"type": "array"}, edges={"type": "array"}, total_reached={"type": "integer"}),
                 lambda a: store.traverse_graph(a.start_node_id, a.relationship_types, a.max_depth, a.direction, a.limit),
                 Capability(input_objects=(ALL_GRAPH,), output_objects=(ALL_GRAPH,), relationships=(ALL_GRAPH,),
                            operations=("multi_hop_traversal", "hierarchy_expansion")), "generic"),
        ToolSpec("find_path",
                 "Shortest chain of relationships connecting two objects, in either edge direction. Use it to discover "
                 "how two things are related.",
                 FindPathArgs, _out(found={"type": "boolean"}, length={"type": "integer"}, steps={"type": "array"}),
                 lambda a: store.find_path(a.source_node_id, a.target_node_id, a.relationship_types, a.max_depth),
                 Capability(input_objects=(ALL_GRAPH,), output_objects=(ALL_GRAPH,), relationships=(ALL_GRAPH,),
                            operations=("path_finding",)), "generic"),
        ToolSpec("query_objects",
                 "List objects of one type whose fields equal the given values. Works for ontology types and for "
                 "operational record types (returns raw rows, not aggregates).",
                 QueryObjectsArgs, _out(objects={"type": "array"}, total_matches={"type": "integer"}),
                 lambda a: store.query_objects(a.object_type, a.filters, a.limit),
                 Capability(entry_objects=(ALL_GRAPH, ALL_OPERATIONAL), output_objects=(ALL_GRAPH, ALL_OPERATIONAL),
                            relationships=(ALL_OPERATIONAL,), operations=("filter_objects",)), "generic"),
        ToolSpec("evaluate_rule",
                 "Decide whether a rule holds for a set of known facts. Returns satisfied true/false, or null with the "
                 "list of missing facts. Being linked to a rule does not mean the rule is met: evaluate it.",
                 EvaluateRuleArgs, _out(satisfied={"type": ["boolean", "null"]}, missing_context={"type": "array"},
                                        required_context={"type": "array"}),
                 evaluate_rule,
                 Capability(input_objects=(ALL_GRAPH,), operations=("rule_evaluation", "conditional_branching")), "generic"),
    ]
    for spec in specs:
        registry.register(spec)
