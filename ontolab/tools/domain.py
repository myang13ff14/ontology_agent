"""Domain-specific ontology tools for the customer-service / troubleshooting domain.

These encapsulate graph traversal behind business-shaped functions, which is the
knowledge-graph access pattern of the Snowflake article. Unlike the generic tools
they know the domain's type and relationship names.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..ontology import rules
from ..ontology.store import OntologyAccessError, OntologyStore
from ..semantic.view import SemanticView
from ..text import normalize, overlap
from .generic import _out
from .registry import Capability, ToolRegistry, ToolSpec

SYMPTOM, CHECKLIST, ACTION, OUTCOME, RULE, ARTICLE, DRIVER = (
    "Symptom", "DiagnosticChecklist", "DiagnosticAction", "ActionOutcome", "DecisionRule", "KnowledgeArticle", "CallDriver")
DRIVER_METRICS = ["case_count", "average_handle_time", "fix_on_phone_rate", "escalation_rate", "work_order_generation_rate"]


class ResolveSymptomArgs(BaseModel):
    term: str = Field(description="How the customer or analyst describes the problem.")


class SymptomIdArgs(BaseModel):
    symptom_id: str = Field(description="Symptom id from resolve_symptom, e.g. S_NOT_CHARGING.")


class ChecklistIdArgs(BaseModel):
    checklist_id: str = Field(description="Checklist id from get_diagnostic_checklist.")


class ActionIdArgs(BaseModel):
    action_id: str = Field(description="Diagnostic action id from get_diagnostic_actions.")


class NextActionArgs(BaseModel):
    action_id: str = Field(description="The diagnostic action that was just performed.")
    outcome: str = Field(description="What happened: an outcome id, code or description from get_possible_outcomes.")
    context: dict[str, Any] = Field(default_factory=dict, description="Known facts that select the branch, such as "
                                    "{\"checklist_id\": \"CL_BLACK_SCREEN\", \"removable_battery\": false}.")


class ConstraintArgs(BaseModel):
    action_id: str = Field(description="Diagnostic action id.")
    context: dict[str, Any] = Field(default_factory=dict, description="Known facts to evaluate the rules against. "
                                    "Leave empty to just list the rules.")


class DriverPerformanceArgs(BaseModel):
    driver_id: str = Field(description="Call driver id, e.g. CD_BATTERY.")
    filters: dict[str, Any] = Field(default_factory=dict, description="Optional case dimension -> value filters, "
                                    "e.g. {\"channel\": \"phone\"}.")


def register_domain_tools(registry: ToolRegistry, store: OntologyStore, semantic: SemanticView) -> None:
    def require(object_id: str, object_type: str) -> dict:
        node = store.get_object(object_id)
        if node["node_type"] != object_type:
            raise OntologyAccessError("wrong_object_type", f"'{object_id}' is a {node['node_type']}, not a {object_type}.",
                                      expected=object_type, actual=node["node_type"])
        return node

    def merge(*results: dict) -> dict:
        nodes, edges = set(), set()
        for r in results:
            nodes.update(r.get("provenance", {}).get("node_ids", []))
            edges.update(r.get("provenance", {}).get("edge_ids", []))
        return {"node_ids": sorted(nodes), "edge_ids": sorted(edges)}

    def resolve_symptom(args: ResolveSymptomArgs) -> dict:
        found = store.search_objects(args.term, SYMPTOM, 5)
        best = found["matches"][0] if found["matches"] else None
        confident = bool(best) and best["score"] >= 0.8 and (len(found["matches"]) == 1 or found["matches"][1]["score"] < best["score"])
        return {"term": args.term, "resolved": best if confident else None,
                "candidates": found["matches"], "ambiguous": bool(found["matches"]) and not confident,
                "provenance": found["provenance"]}

    def get_symptom_hierarchy(args: SymptomIdArgs) -> dict:
        require(args.symptom_id, SYMPTOM)
        h = store.hierarchy(args.symptom_id)
        return {"symptom": h["object"], "ancestors": h["ancestors"], "descendants": h["descendants"],
                "provenance": h["provenance"]}

    def get_diagnostic_checklist(args: SymptomIdArgs) -> dict:
        symptom = require(args.symptom_id, SYMPTOM)
        lineage = [symptom] + store.hierarchy(args.symptom_id)["ancestors"]  # nearest ancestor first
        collected = []
        for candidate in lineage:
            found = store.get_neighbors(candidate["node_id"], "HAS_CHECKLIST", "out")
            collected.append(found)
            if found["neighbors"]:
                inherited = candidate["node_id"] != args.symptom_id
                return {
                    "symptom": {k: symptom[k] for k in ("node_id", "canonical_name")},
                    "checklists": [{"checklist_id": n["node_id"], "name": n["canonical_name"]} for n in found["neighbors"]],
                    "inherited_from": {"node_id": candidate["node_id"], "canonical_name": candidate["canonical_name"]} if inherited else None,
                    "provenance": merge(*collected),
                }
        return {"symptom": {k: symptom[k] for k in ("node_id", "canonical_name")}, "checklists": [], "inherited_from": None,
                "note": "No checklist is defined for this symptom or any of its parent symptoms.",
                "provenance": merge(*collected)}

    def get_diagnostic_actions(args: ChecklistIdArgs) -> dict:
        checklist = require(args.checklist_id, CHECKLIST)
        found = store.get_neighbors(args.checklist_id, "CONTAINS", "out")
        ordered = sorted(found["neighbors"], key=lambda n: (n["edge_properties"].get("step_order", 0), n["node_id"]))
        details = store.get_objects([n["node_id"] for n in ordered], brief=False)
        return {
            "checklist": {"checklist_id": checklist["node_id"], "name": checklist["canonical_name"]},
            "actions": [{"step": n["edge_properties"].get("step_order"), "action_id": n["node_id"],
                         "name": n["canonical_name"], "definition": details[n["node_id"]]["definition"],
                         **details[n["node_id"]]["properties"]} for n in ordered],
            "provenance": found["provenance"],
        }

    def outcomes_of(action_id: str) -> tuple[list[dict], dict]:
        found = store.get_neighbors(action_id, "HAS_OUTCOME", "out")
        details = store.get_objects([n["node_id"] for n in found["neighbors"]], brief=False)
        return [{"outcome_id": n["node_id"], "name": n["canonical_name"],
                 "code": details[n["node_id"]]["properties"].get("code"),
                 "resolution": details[n["node_id"]]["properties"].get("resolution"),
                 "synonyms": details[n["node_id"]]["synonyms"]} for n in found["neighbors"]], found

    def get_possible_outcomes(args: ActionIdArgs) -> dict:
        action = require(args.action_id, ACTION)
        outcomes, found = outcomes_of(args.action_id)
        return {"action": {"action_id": action["node_id"], "name": action["canonical_name"]},
                "outcomes": [{k: o[k] for k in ("outcome_id", "name", "code", "resolution")} for o in outcomes],
                "provenance": found["provenance"]}

    def constraints_of(action_id: str, context: dict) -> tuple[list[dict], dict]:
        found = store.get_neighbors(action_id, "GOVERNED_BY", "out")
        details = store.get_objects([n["node_id"] for n in found["neighbors"]], brief=False)
        out = []
        for n in found["neighbors"]:
            properties = details[n["node_id"]]["properties"]
            entry = {"rule_id": n["node_id"], "name": n["canonical_name"], "category": properties.get("category"),
                     "expression": properties.get("expression"), "failure_message": properties.get("failure_message"),
                     "required_context": rules.referenced_names(properties["expression"])}
            if context:
                entry["evaluation"] = rules.evaluate(properties["expression"], context)
            out.append(entry)
        return out, found

    def get_next_action(args: NextActionArgs) -> dict:
        action = require(args.action_id, ACTION)
        outcomes, found_outcomes = outcomes_of(args.action_id)
        wanted = normalize(args.outcome)

        def score(o: dict) -> float:
            if wanted in (o["outcome_id"].lower(), normalize(o["code"] or ""), normalize(o["name"])):
                return 1.0
            return max(overlap(args.outcome, t) for t in [o["name"], o["code"] or ""] + o["synonyms"])
        ranked = sorted(outcomes, key=lambda o: (-score(o), o["outcome_id"]))
        if not ranked or score(ranked[0]) < 0.5 or (len(ranked) > 1 and score(ranked[1]) == score(ranked[0])):
            raise OntologyAccessError("unknown_outcome", f"'{args.outcome}' does not identify one outcome of {args.action_id}.",
                                      valid_outcomes=[{k: o[k] for k in ("outcome_id", "code", "name")} for o in outcomes])
        outcome = ranked[0]
        branches = store.get_neighbors(outcome["outcome_id"], "LEADS_TO", "out")
        matched, undetermined, rejected = [], [], []
        for n in branches["neighbors"]:
            condition = n["edge_properties"].get("condition")
            entry = {"action_id": n["node_id"], "name": n["canonical_name"], "condition": condition, "edge_id": n["edge_id"]}
            verdict = rules.evaluate(condition, args.context) if condition else {"satisfied": True, "missing_context": []}
            if verdict["satisfied"] is True:
                entry["constraints"] = constraints_of(n["node_id"], args.context)[0]
                matched.append(entry)
            elif verdict["satisfied"] is None:
                undetermined.append({**entry, "missing_context": verdict["missing_context"]})
            else:
                rejected.append(entry)
        note = None
        if outcome["resolution"] != "continue":
            note = f"This outcome ends the procedure (resolution: {outcome['resolution']}); there is no next action."
        elif not matched and undetermined:
            note = "The next action depends on context you did not supply; see missing_context on each candidate."
        elif not matched:
            note = "No branch applies for this context."
        return {
            "action": {"action_id": action["node_id"], "name": action["canonical_name"]},
            "outcome": {k: outcome[k] for k in ("outcome_id", "name", "code", "resolution")},
            "context_used": args.context, "next_actions": matched, "undetermined_branches": undetermined,
            "branches_not_applicable": rejected, "note": note,
            "provenance": merge(found_outcomes, branches),
        }

    def get_action_constraints(args: ConstraintArgs) -> dict:
        action = require(args.action_id, ACTION)
        constraints, found = constraints_of(args.action_id, args.context)
        eligible = None
        if args.context and constraints:
            verdicts = [c["evaluation"]["satisfied"] for c in constraints]
            eligible = False if False in verdicts else (None if None in verdicts else True)
        elif not constraints:
            eligible = True
        return {"action": {"action_id": action["node_id"], "name": action["canonical_name"]},
                "constraints": constraints, "eligible": eligible,
                "eligibility_note": "true = all rules satisfied, false = a rule is violated, null = not evaluated or facts missing.",
                "provenance": found["provenance"]}

    def get_related_knowledge(args: ActionIdArgs) -> dict:
        action = require(args.action_id, ACTION)
        found = store.get_neighbors(args.action_id, "DESCRIBES", "in")
        details = store.get_objects([n["node_id"] for n in found["neighbors"]], brief=False)
        articles, extra = [], []
        for n in found["neighbors"]:
            replaced_by = store.get_neighbors(n["node_id"], "SUPERSEDES", "in")
            extra.append(replaced_by)
            properties = details[n["node_id"]]["properties"]
            articles.append({"article_id": n["node_id"], "title": n["canonical_name"], "status": properties.get("status"),
                             "last_reviewed": properties.get("last_reviewed"), "summary": properties.get("summary"),
                             "url": properties.get("url"),
                             "superseded_by": [r["node_id"] for r in replaced_by["neighbors"]]})
        articles.sort(key=lambda a: (a["status"] != "current", a["article_id"]))
        return {"action": {"action_id": action["node_id"], "name": action["canonical_name"]}, "articles": articles,
                "note": "Prefer articles with status 'current'. Superseded or outdated articles may conflict with them.",
                "provenance": merge(found, *extra)}

    def get_driver_performance(args: DriverPerformanceArgs) -> dict:
        driver = require(args.driver_id, DRIVER)
        filters = [{"dimension": "call_driver_id", "operator": "=", "value": args.driver_id}]
        for dimension, value in args.filters.items():
            filters.append({"dimension": dimension, "operator": "in" if isinstance(value, list) else "=", "value": value})
        result = semantic.run({"metrics": DRIVER_METRICS, "filters": filters})
        return {"call_driver": {"driver_id": driver["node_id"], "name": driver["canonical_name"]},
                "metrics": result["rows"][0] if result["rows"] else {}, "metric_definitions": result["metric_definitions"],
                "sql": result["sql"], "data_notice": result["data_notice"],
                "provenance": {"node_ids": [args.driver_id], "edge_ids": []}}

    D = "domain"
    specs = [
        ToolSpec("resolve_symptom",
                 "Turn a description of a laptop problem into a symptom from the ontology, using names and synonyms. "
                 "Returns the resolved symptom or ranked candidates when it is ambiguous. Start here for symptom questions.",
                 ResolveSymptomArgs, _out(resolved={"type": ["object", "null"]}, candidates={"type": "array"}, ambiguous={"type": "boolean"}),
                 resolve_symptom, Capability(entry_objects=(SYMPTOM,), output_objects=(SYMPTOM,), operations=("resolve_term",)), D),
        ToolSpec("get_symptom_hierarchy",
                 "Broader (ancestor) and more specific (descendant) symptoms of a symptom. Use it to expand a symptom "
                 "category into all the specific symptoms under it.",
                 SymptomIdArgs, _out(ancestors={"type": "array"}, descendants={"type": "array"}),
                 get_symptom_hierarchy, Capability(input_objects=(SYMPTOM,), output_objects=(SYMPTOM,), relationships=("SUBTYPE_OF",),
                                                   operations=("hierarchy_expansion",)), D),
        ToolSpec("get_diagnostic_checklist",
                 "The troubleshooting checklist for a symptom. If the symptom has none, the nearest parent symptom's "
                 "checklist is returned and inherited_from says where it came from.",
                 SymptomIdArgs, _out(checklists={"type": "array"}, inherited_from={"type": ["object", "null"]}),
                 get_diagnostic_checklist, Capability(input_objects=(SYMPTOM,), output_objects=(CHECKLIST,),
                                                      relationships=("HAS_CHECKLIST", "SUBTYPE_OF"),
                                                      operations=("neighbor_lookup", "hierarchy_expansion")), D),
        ToolSpec("get_diagnostic_actions",
                 "The ordered diagnostic actions (steps) in a checklist, with each action's time, risk and kind.",
                 ChecklistIdArgs, _out(actions={"type": "array"}),
                 get_diagnostic_actions, Capability(input_objects=(CHECKLIST,), output_objects=(ACTION,), relationships=("CONTAINS",),
                                                    operations=("neighbor_lookup",)), D),
        ToolSpec("get_possible_outcomes",
                 "The possible results of performing a diagnostic action, and whether each one ends the procedure.",
                 ActionIdArgs, _out(outcomes={"type": "array"}),
                 get_possible_outcomes, Capability(input_objects=(ACTION,), output_objects=(OUTCOME,), relationships=("HAS_OUTCOME",),
                                                   operations=("neighbor_lookup",)), D),
        ToolSpec("get_next_action",
                 "What to do after a diagnostic action ended in a given outcome. The answer can depend on context such "
                 "as which checklist is being followed, so pass what you know; missing facts are reported back.",
                 NextActionArgs, _out(next_actions={"type": "array"}, undetermined_branches={"type": "array"}, note={"type": ["string", "null"]}),
                 get_next_action, Capability(input_objects=(ACTION,), output_objects=(ACTION, OUTCOME), relationships=("HAS_OUTCOME", "LEADS_TO"),
                                             operations=("conditional_branching", "neighbor_lookup")), D),
        ToolSpec("get_action_constraints",
                 "The decision rules (safety, policy, prerequisites) that govern a diagnostic action. With context, each "
                 "rule is evaluated and overall eligibility is returned.",
                 ConstraintArgs, _out(constraints={"type": "array"}, eligible={"type": ["boolean", "null"]}),
                 get_action_constraints, Capability(input_objects=(ACTION,), output_objects=(RULE,), relationships=("GOVERNED_BY",),
                                                    operations=("neighbor_lookup", "rule_evaluation")), D),
        ToolSpec("get_related_knowledge",
                 "Knowledge articles that describe how to perform a diagnostic action, with their status (current, "
                 "outdated, superseded) and review date.",
                 ActionIdArgs, _out(articles={"type": "array"}),
                 get_related_knowledge, Capability(input_objects=(ACTION,), output_objects=(ARTICLE,), relationships=("DESCRIBES", "SUPERSEDES"),
                                                   operations=("neighbor_lookup",)), D),
        ToolSpec("get_driver_performance",
                 "Operational performance of one call driver: case count, average handle time, fix-on-phone rate, "
                 "escalation rate and work order generation rate.",
                 DriverPerformanceArgs, _out(metrics={"type": "object"}, sql={"type": "string"}),
                 get_driver_performance, Capability(input_objects=(DRIVER,), output_objects=("ServiceCase",), relationships=("HAS_DRIVER",),
                                                    operations=("metric_aggregation",)), D),
    ]
    for spec in specs:
        registry.register(spec)
