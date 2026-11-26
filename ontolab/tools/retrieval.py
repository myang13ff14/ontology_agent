"""Semantic-view, profile-search and terminology tools."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..graphrag.profiles import ProfileIndex
from ..graphrag.terminology import Terminology
from ..semantic.view import SemanticQuery, SemanticView
from .generic import NoArgs, _out
from .registry import (ALL_GRAPH, ALL_OPERATIONAL, OPERATIONAL_TARGETS, Capability, ToolRegistry, ToolSpec)


class ProfileSearchArgs(BaseModel):
    query: str = Field(description="What you are looking for, in natural language.")
    top_k: int = Field(default=3, ge=1, le=8, description="How many profiles to return.")
    object_type: str | None = Field(default=None, description="Restrict to one object type, e.g. Symptom.")


class TerminologyArgs(BaseModel):
    term: str = Field(description="The phrase to look up, as the user wrote it.")


def register_semantic_tool(registry: ToolRegistry, semantic: SemanticView) -> None:
    registry.register(ToolSpec(
        "query_semantic_view",
        "Query operational support data (service cases, diagnostic executions, work orders) through the governed "
        "semantic view. Ask for metrics and dimensions by name; the layer writes and runs the SQL. All metrics in one "
        "call must belong to the same logical table. Filter on *_id dimensions with ontology ids. Call with only "
        "dimensions to list their values (for example every symptom_id with its symptom_name).\n" + semantic.describe_for_prompt(),
        SemanticQuery, _out(sql={"type": "string"}, rows={"type": "array"}, row_count={"type": "integer"},
                            metric_definitions={"type": "object"}),
        lambda a: {**semantic.run(a), "provenance": {"node_ids": [], "edge_ids": []}},
        Capability(entry_objects=(ALL_OPERATIONAL, OPERATIONAL_TARGETS), output_objects=(ALL_OPERATIONAL, OPERATIONAL_TARGETS),
                   relationships=(ALL_OPERATIONAL,), operations=("metric_aggregation",)), "semantic"))
    registry.register(ToolSpec(
        "describe_semantic_view",
        "Full definition of the semantic view: logical tables, joins, dimensions, facts, and each metric's formula, "
        "numerator and denominator.",
        NoArgs, _out(logical_tables={"type": "array"}, metrics={"type": "array"}),
        lambda a: {**semantic.catalog(), "provenance": {"node_ids": [], "edge_ids": []}},
        Capability(operations=("schema_discovery",)), "semantic"))


def register_graphrag_tools(registry: ToolRegistry, index: ProfileIndex, terminology: Terminology) -> None:
    registry.register(ToolSpec(
        "search_ontology_profiles",
        "Search precomputed concept profiles of the ontology. Each profile is a complete, flattened description of one "
        "concept: definition, synonyms, parent and child concepts, all descendants, directly linked objects, attributes "
        "aggregated across descendants, and related procedures, rules and articles, all with ids. Use the ids you find "
        "(for example descendant symptom ids) as filters in query_semantic_view. No graph traversal is needed.",
        ProfileSearchArgs, _out(results={"type": "array"}, profile_version={"type": "string"}, stale={"type": "boolean"}),
        lambda a: index.search(a.query, a.top_k, a.object_type),
        Capability(entry_objects=(ALL_GRAPH,), output_objects=(ALL_GRAPH,), relationships=(ALL_GRAPH,),
                   operations=("profile_retrieval", "resolve_term", "object_details", "hierarchy_expansion",
                               "neighbor_lookup", "multi_hop_traversal", "conditional_branching"),
                   access="precomputed"), "graphrag"))
    registry.register(ToolSpec(
        "resolve_terminology",
        "Look up a business or colloquial phrase in the curated terminology mappings. Returns the concepts it maps to "
        "and whether the mapping is authoritative, a named collection of concepts, or ambiguous (in which case ask the "
        "user the clarification question instead of guessing).",
        TerminologyArgs, _out(matches={"type": "array"}, mapping_version={"type": "string"}),
        lambda a: terminology.resolve(a.term),
        Capability(entry_objects=tuple(terminology.target_types()), output_objects=tuple(terminology.target_types()),
                   operations=("terminology_mapping", "resolve_term"), access="precomputed"), "terminology"))
