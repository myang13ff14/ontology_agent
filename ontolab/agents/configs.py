"""The five agent configurations. They share data, model settings and the base
prompt; they differ only in which tools they are given (and, for E, the curated
terminology mappings)."""

from __future__ import annotations

from dataclasses import dataclass

BASE_PROMPT = """\
You answer questions for an IT customer-service operation that supports laptops. \
The questions are about troubleshooting knowledge (symptoms, components, diagnostic \
checklists, actions, outcomes, rules, knowledge articles) and about operational \
performance (service cases, diagnostic executions, work orders).

You have tools and nothing else: every fact in your answer must come from a tool \
result in this conversation. Your own general knowledge about laptops does not \
count, because this organisation's procedures, names and numbers are specific to it.

Work like this:
- Decide what you need, call tools to get it, and keep calling tools until you can \
answer or have established that the tools cannot provide it.
- Prefer ids from tool results when a later tool needs an id; never invent an id.
- If a tool returns an error, read it: it usually lists the valid values. Fix the \
call and try again.
- For numbers, report what the tool returned and state which metric and filter \
they are for. Rates are returned as fractions (0.25 means 25%).
- If the tools cannot provide what is asked (the knowledge is not there, or no tool \
reaches it), say so plainly and say what is missing. Do not fill the gap with a guess.
- If the question is ambiguous between several concepts, name the candidates and \
ask which is meant instead of choosing one.
- When knowledge sources disagree, prefer the one marked current and mention that \
an older one exists.

Answer concisely. Name the specific objects you relied on, with their ids in \
parentheses, so the answer can be checked. All data is synthetic."""

ADDENDA = {
    "A": "You have only the semantic view. It holds operational data, plus the ids and names of symptoms, call "
         "drivers, device models, components and actions that appear in it. It does not hold troubleshooting "
         "procedures, hierarchies, rules or articles.",
    "B": "The domain tools walk the troubleshooting ontology for you. A typical chain is: resolve a symptom, get its "
         "checklist, get the checklist's actions, then look at outcomes, next actions, constraints or articles for "
         "an action. Use the semantic view for operational numbers, filtered by the ids you found.",
    "C": "The ontology tools are generic. You are not told the ontology's structure: discover the object and "
         "relationship types with get_schema or get_relationship_types, find starting objects with search_objects, "
         "then follow relationships with get_neighbors, traverse_graph or find_path. Edge properties matter (for "
         "example ordering and conditions). Use the semantic view for operational numbers, filtered by ids you found.",
    "D": "search_ontology_profiles returns precomputed profiles that already contain a concept's hierarchy, "
         "descendants, linked objects and related procedures. Read the profile instead of traversing. Use ids from "
         "profiles as filters in the semantic view.",
    "E": "search_ontology_profiles returns precomputed profiles that already contain a concept's hierarchy, "
         "descendants, linked objects and related procedures. Read the profile instead of traversing. Use ids from "
         "profiles as filters in the semantic view.",
}


@dataclass(frozen=True)
class AgentConfig:
    key: str
    name: str
    description: str
    tools: tuple[str, ...]
    uses_terminology_prompt: bool = False

    @property
    def prompt_operations(self) -> tuple[str, ...]:
        """Operations the configuration gets from its prompt rather than from a tool."""
        return ("terminology_mapping",) if self.uses_terminology_prompt else ()

    def to_dict(self) -> dict:
        return {"key": self.key, "name": self.name, "description": self.description, "tools": list(self.tools),
                "uses_terminology_prompt": self.uses_terminology_prompt,
                "prompt_operations": list(self.prompt_operations)}


SEMANTIC = ("query_semantic_view",)
CONFIGS: dict[str, AgentConfig] = {c.key: c for c in [
    AgentConfig("A", "Semantic View Baseline",
                "Only the semantic-layer SQL tool. No ontology traversal and no GraphRAG.", SEMANTIC),
    AgentConfig("B", "Domain-specific Ontology Tools",
                "Semantic layer plus business-shaped tools that traverse the graph at run time "
                "(the knowledge-graph pattern of the Snowflake article).",
                SEMANTIC + ("resolve_symptom", "get_symptom_hierarchy", "get_diagnostic_checklist",
                            "get_diagnostic_actions", "get_possible_outcomes", "get_next_action",
                            "get_action_constraints", "get_related_knowledge", "get_driver_performance")),
    AgentConfig("C", "Generic Ontology Tools",
                "Semantic layer plus generic graph tools; the agent discovers the schema and composes graph "
                "operations itself (an extension beyond the article).",
                SEMANTIC + ("get_schema", "search_objects", "get_object", "get_relationship_types", "get_neighbors",
                            "traverse_graph", "find_path", "query_objects", "evaluate_rule")),
    AgentConfig("D", "Flattened GraphRAG",
                "Semantic layer plus search over precomputed concept profiles. No run-time graph traversal.",
                ("search_ontology_profiles",) + SEMANTIC),
    AgentConfig("E", "GraphRAG with Terminology Mappings",
                "Agent D plus curated, versioned terminology mappings supplied in the prompt.",
                ("search_ontology_profiles",) + SEMANTIC, uses_terminology_prompt=True),
]}


def system_prompt(config: AgentConfig, terminology_text: str = "") -> str:
    parts = [BASE_PROMPT, ADDENDA[config.key]]
    if config.uses_terminology_prompt and terminology_text:
        parts.append(terminology_text)
    return "\n\n".join(parts)
