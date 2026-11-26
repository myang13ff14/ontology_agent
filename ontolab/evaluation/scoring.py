"""Scoring one agent run against one benchmark task.

Deterministic, rule-based scoring (no LLM judge), in three parts that mirror the
evaluation dimensions:

  answer        was the final answer right (0, 1 or 2 points, as in the Snowflake article)
  tool use      given that the tools could do the task, were they used correctly
  attribution   for a failed run, where the failure came from

The heuristics are documented in the README. They are conservative text matches,
so treat single-run verdicts as indicative and read the trace when it matters.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..services import Services
from ..text import normalize
from .benchmark import Task

ARGUMENT_ERROR_CODES = {
    "invalid_arguments", "invalid_argument", "object_not_found", "wrong_object_type", "unknown_object_type",
    "unknown_relationship_type", "unknown_field", "unknown_metric", "unknown_dimension", "unknown_outcome",
    "invalid_filter", "invalid_order_by", "mixed_tables", "empty_query", "not_a_rule", "invalid_expression",
    "not_a_graph_type",
}
SELECTION_ERROR_CODES = {"unknown_tool"}
FAILURE_CATEGORIES = [
    "missing_ontology_knowledge", "missing_ontology_relationship", "insufficient_tool_coverage",
    "incorrect_tool_selection", "incorrect_tool_arguments", "incorrect_tool_sequence", "backend_execution_error",
    "retrieval_failure", "incorrect_result_interpretation", "ungrounded_final_answer",
]

_ABSTAIN = re.compile(
    r"\b(cannot|can't|can not|unable|not able|no tool|not available|unavailable|do not have|don't have|"
    r"does not (contain|include|have|exist|provide)|doesn't (contain|include|have|exist|provide)|"
    r"not (defined|provided|supported|possible|present|recorded|found|covered|something)|"
    r"no (checklist|information|data|record|article|articles|knowledge|such|way)|"
    r"isn't (defined|available)|is not (defined|available)|there (is|are) no|none (is|are|exists?))\b", re.I)
_CLARIFY = re.compile(
    r"\b(which (one|of|do|did|is)|clarify|do you mean|did you mean|could you (confirm|tell|specify|clarify)|"
    r"can you (confirm|tell|specify|clarify)|ambiguous|please (confirm|specify|clarify)|need to know|"
    r"depends on whether)\b", re.I)
_NUMBER = re.compile(r"(?<![A-Za-z0-9_])-?\d[\d,]*(?:\.\d+)?%?")


def id_in(text: str, node_id: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(node_id)}(?![A-Za-z0-9_])", text) is not None


def phrase_in(normalized_text: str, phrase: str) -> bool:
    p = normalize(phrase)
    return bool(p) and re.search(rf"(?<![a-z0-9]){re.escape(p)}(?![a-z0-9])", normalized_text) is not None


def numbers_in(text: str) -> list[tuple[float, bool]]:
    """Numbers in a text as (value, written_as_percent)."""
    out = []
    for token in _NUMBER.findall(text):
        percent = token.endswith("%")
        try:
            out.append((float(token.rstrip("%").replace(",", "")), percent))
        except ValueError:
            continue
    return out


def number_matches(value: float, fmt: str, found: list[tuple[float, bool]]) -> bool:
    """Is `value` stated among the found numbers, allowing for rounding and percent form?"""
    if value is None:
        return False
    for number, percent in found:
        if fmt == "ratio":
            if abs((number / 100 if percent else number) - value) <= 0.006 or abs(number / 100 - value) <= 0.006:
                return True
        elif fmt == "integer":
            if not percent and abs(number - value) < 0.5:
                return True
        elif abs(number - value) <= max(0.06, 0.006 * abs(value)):
            return True
    return False


class Scorer:
    def __init__(self, services: Services):
        self.services = services
        self.nodes = {n["node_id"]: n for n in services.store.all_nodes()}
        self.edges = {(e["source_node_id"], e["relationship_type"], e["target_node_id"]) for e in services.store.all_edges()}
        self._reference_cache: dict[str, list[dict]] = {}

    # -------------------------------------------------------------- mentions

    def names(self, node_id: str, synonyms: bool = True) -> list[str]:
        node = self.nodes.get(node_id)
        if not node:
            return []
        return [node["canonical_name"]] + (node["synonyms"] if synonyms else [])

    def mentions(self, raw: str, normalized: str, node_id: str, synonyms: bool = True) -> bool:
        return id_in(raw, node_id) or any(phrase_in(normalized, n) for n in self.names(node_id, synonyms))

    # ------------------------------------------------------- expected values

    def reference_items(self, task: Task) -> list[dict]:
        """Live expected numbers / names from the task's reference queries."""
        if task.id in self._reference_cache:
            return self._reference_cache[task.id]
        items = []
        for reference in task.expected.reference_queries:
            result = self.services.semantic.run(reference.query)
            if reference.check == "top_dimension":
                for row in result["rows"][: reference.top_n]:
                    items.append({"kind": "name", "value": str(row[reference.dimension]), "label": reference.dimension})
            else:
                for row in result["rows"]:
                    for metric in reference.query.get("metrics", []):
                        fmt = self.services.semantic.metrics[metric].get("format", "number")
                        label = metric + "".join(f" [{row[d]}]" for d in reference.query.get("dimensions", []))
                        items.append({"kind": "number", "value": row[metric], "format": fmt, "label": label})
        self._reference_cache[task.id] = items
        return items

    # ---------------------------------------------------------------- answer

    def score_answer(self, task: Task, answer: str) -> dict:
        expected = task.expected
        normalized = normalize(answer)
        found_numbers = numbers_in(answer)
        hits, misses = [], []
        for node_id in expected.answer_ids:
            (hits if self.mentions(answer, normalized, node_id) else misses).append(node_id)
        for group in expected.answer_text:
            (hits if any(phrase_in(normalized, p) or p.lower() in answer.lower() for p in group) else misses).append("|".join(group))
        for item in self.reference_items(task):
            ok = (phrase_in(normalized, item["value"]) if item["kind"] == "name"
                  else number_matches(item["value"], item["format"], found_numbers))
            (hits if ok else misses).append(f"{item['label']}={item['value']}")
        total = len(hits) + len(misses)
        recall = 1.0 if total == 0 else len(hits) / total
        forbidden = [n for n in expected.forbidden_ids if self.mentions(answer, normalized, n)]
        # Over-inclusion guard: naming many other objects of the same kinds is not a correct answer,
        # even if the right ones are somewhere in the list.
        kinds = {self.nodes[i]["node_type"] for i in expected.answer_ids if i in self.nodes}
        extras = [node_id for node_id, node in self.nodes.items()
                  if node["node_type"] in kinds and node_id not in expected.answer_ids
                  and (id_in(answer, node_id) or (len(node["canonical_name"].split()) >= 2
                                                  and phrase_in(normalized, node["canonical_name"])))]
        allowed_extras = expected.max_extra if expected.max_extra is not None else len(expected.answer_ids) + 3
        over_inclusive = bool(expected.answer_ids) and len(extras) > allowed_extras
        abstained = bool(_ABSTAIN.search(answer))
        asked = "?" in answer or bool(_CLARIFY.search(answer))

        if not answer.strip():
            score = 0
        elif expected.behavior == "abstain":
            score = 2 if abstained else 0
        elif expected.behavior == "clarify":
            enough = recall >= expected.min_recall
            score = 2 if (enough and asked) else (1 if (enough or (asked and recall >= 0.5)) else 0)
        else:
            if recall >= expected.min_recall and not forbidden and not over_inclusive:
                score = 2
            elif recall >= 0.5:
                score = 1
            else:
                score = 0
        return {"score": score, "recall": round(recall, 3), "hits": hits, "misses": misses, "forbidden_hits": forbidden,
                "extra_objects": extras, "over_inclusive": over_inclusive,
                "abstained": abstained, "asked_clarification": asked, "expected_behavior": expected.behavior}

    # ---------------------------------------------------------- groundedness

    def groundedness(self, answer: str, grounding_text: str) -> dict:
        """Share of the answer's checkable statements that appear in what the agent was shown."""
        normalized_answer, normalized_ground = normalize(answer), normalize(grounding_text)
        mentioned, ungrounded = [], []
        for node_id, node in self.nodes.items():
            name = node["canonical_name"]
            multiword = len(name.split()) >= 2  # single common words ("Battery") are too generic to judge
            if id_in(answer, node_id) or (multiword and phrase_in(normalized_answer, name)):
                mentioned.append(node_id)
                if not (id_in(grounding_text, node_id) or phrase_in(normalized_ground, name)):
                    ungrounded.append(node_id)
        ground_numbers = numbers_in(grounding_text)
        figures = [(n, p) for n, p in numbers_in(answer) if p or n != int(n)]
        ungrounded_numbers = []
        for number, percent in figures:
            fraction = number / 100 if percent else number
            ok = any(abs(g - fraction) <= max(0.006, 0.006 * abs(fraction)) or abs(g - number) <= max(0.06, 0.006 * abs(number))
                     or abs(g * 100 - number) <= 0.6 for g, _ in ground_numbers)
            if not ok:
                ungrounded_numbers.append(f"{number:g}{'%' if percent else ''}")
        total = len(mentioned) + len(figures)
        bad = len(ungrounded) + len(ungrounded_numbers)
        return {
            "groundedness": 1.0 if total == 0 else round(1 - bad / total, 3),
            "mentioned_objects": len(mentioned), "ungrounded_objects": ungrounded,
            "ungrounded_numbers": ungrounded_numbers,
            # Derived figures (differences, sums) are legitimately absent from tool output, so numbers
            # only count as hallucination when the agent had no numeric tool output at all.
            "hallucinated": bool(ungrounded) or (bool(ungrounded_numbers) and not ground_numbers),
        }

    # -------------------------------------------------------------- tool use

    def score_tool_use(self, task: Task, trace: dict, usable_sequences: list[list[str]]) -> dict:
        calls = trace["tool_calls"]
        called = [c["tool"] for c in calls]

        def recall_of(sequence: list[str]) -> float:
            pool = list(called)
            matched = 0
            for tool in sequence:
                if tool in pool:
                    pool.remove(tool)
                    matched += 1
            return matched / len(sequence) if sequence else 1.0

        best = max(usable_sequences, key=lambda s: (recall_of(s), -len(s)), default=[])
        union = {t for s in usable_sequences for t in s}
        selection_errors = [c for c in calls if not c["ok"] and c["error"]["code"] in SELECTION_ERROR_CODES]
        argument_errors = [c for c in calls if not c["ok"] and c["error"]["code"] in ARGUMENT_ERROR_CODES]
        backend_errors = [c for c in calls if not c["ok"] and c["error"]["code"] not in ARGUMENT_ERROR_CODES | SELECTION_ERROR_CODES]

        if not usable_sequences:
            selection = 1.0 if not called else 0.0
        elif not called:
            selection = 0.0
        else:
            selection = sum(1 for t in called if t in union) / len(called)
        valid_rate = 1.0 if not calls else 1 - len(argument_errors) / len(calls)
        checks = {tool: needles for tool, needles in task.argument_checks.items() if tool in called}
        passed = 0
        for tool, needles in checks.items():
            if any(c["tool"] == tool and all(n in json.dumps(c["arguments"], default=str) for n in needles) for c in calls):
                passed += 1
        check_rate = passed / len(checks) if checks else None
        argument_accuracy = valid_rate if check_rate is None else (valid_rate + check_rate) / 2

        return {
            "called": called, "best_sequence": best,
            "tool_selection_accuracy": round(selection, 3),
            "required_tool_recall": round(recall_of(best), 3) if usable_sequences else None,
            "unnecessary_tool_calls": sum(1 for t in called if t not in set(best)) if usable_sequences else len(called),
            "argument_accuracy": round(argument_accuracy, 3),
            "argument_checks_passed": check_rate,
            "sequencing_correctness": round(_lcs(best, called) / len(best), 3) if best else None,
            "selection_errors": len(selection_errors), "argument_errors": len(argument_errors),
            "backend_errors": len(backend_errors),
            "multi_step": len(best) >= 2,
        }

    def evidence(self, task: Task, results_text: str) -> dict:
        """Did the tool results contain what the answer needs?"""
        ids = task.evidence_ids()
        missing_ids = [i for i in ids if not id_in(results_text, i)]
        found_numbers = numbers_in(results_text)
        normalized = normalize(results_text) if any(i["kind"] == "name" for i in self.reference_items(task)) else ""
        missing_values = []
        for item in self.reference_items(task):
            ok = (phrase_in(normalized, item["value"]) if item["kind"] == "name"
                  else number_matches(item["value"], item["format"], found_numbers))
            if not ok:
                missing_values.append(item["label"])
        defined = bool(ids or self.reference_items(task))
        return {"defined": defined, "retrieved": (not missing_ids and not missing_values) if defined else None,
                "missing_ids": missing_ids, "missing_values": missing_values}

    # ------------------------------------------------------------- one run

    def score(self, task: Task, trace: dict, coverage: dict, grounding_extra: str = "") -> dict:
        answer = trace["final_answer"] or ""
        results_text = "\n".join(json.dumps(c["result"], default=str) for c in trace["tool_calls"] if c["ok"])
        answer_eval = self.score_answer(task, answer)
        tool_eval = self.score_tool_use(task, trace, coverage["usable_sequences"])
        evidence = self.evidence(task, results_text)
        grounded = self.groundedness(answer, "\n".join([results_text, grounding_extra, task.question]))
        covered = coverage["covered"]
        completed = trace["stop_reason"] == "answered" and bool(answer.strip())

        covered_in_practice = False
        if covered:
            score = answer_eval["score"]
        elif task.expected.behavior == "abstain":
            score = answer_eval["score"]
        elif answer_eval["score"] == 2 and not grounded["hallucinated"]:
            # The declared capabilities said "not covered", yet the agent produced a correct, grounded
            # answer: the coverage model was too conservative for this task. Count it, and flag it.
            score, covered_in_practice = 2, True
        else:
            score = 2 if (answer_eval["abstained"] and completed) else 0
        if trace["stop_reason"] == "llm_error":
            score = 0
        success = score == 2

        # A correct, grounded answer reached by a route the benchmark did not list is not a tool-use error.
        alternative = bool(covered and success and not grounded["hallucinated"]
                           and (tool_eval["required_tool_recall"] or 0) < 1)
        if alternative:
            tool_eval.update(tool_selection_accuracy=1.0, required_tool_recall=1.0, sequencing_correctness=1.0)
        tool_eval["alternative_sequence"] = alternative
        argument_ok = tool_eval["argument_checks_passed"] in (None, 1.0) and tool_eval["argument_errors"] == 0
        tool_use_correct = None
        if covered and task.expected.behavior != "abstain" and coverage["usable_sequences"]:
            tool_use_correct = bool(tool_eval["required_tool_recall"] == 1 and tool_eval["sequencing_correctness"] == 1
                                    and (argument_ok or alternative) and tool_eval["backend_errors"] == 0)
        tool_eval["tool_use_correct"] = tool_use_correct
        tool_eval["multi_step_success"] = (tool_use_correct if tool_eval["multi_step"] and tool_use_correct is not None else None)

        failure = None if success else self.attribute(task, trace, coverage, answer_eval, tool_eval, evidence, grounded)
        return {
            "task_id": task.id, "config": trace["config"], "covered": covered, "covered_in_practice": covered_in_practice,
            "score": score, "success": success, "completed": completed, "stop_reason": trace["stop_reason"],
            "answer": answer_eval, "tool_use": tool_eval, "evidence": evidence, "grounding": grounded,
            "failure": failure,
            "tool_call_count": trace["metrics"]["tool_call_count"], "input_tokens": trace["metrics"]["input_tokens"],
            "output_tokens": trace["metrics"]["output_tokens"], "latency_ms": trace["metrics"]["latency_ms"],
        }

    # ----------------------------------------------------------- attribution

    def attribute(self, task: Task, trace: dict, coverage: dict, answer_eval: dict, tool_eval: dict,
                  evidence: dict, grounded: dict) -> dict:
        """Name the layer a failure came from: ontology, access layer (tools), or the LLM."""
        causes: list[tuple[str, str]] = []
        missing_nodes = [i for i in task.evidence_ids() if i not in self.nodes]
        missing_edges = [e for e in task.expected.evidence_edges if tuple(e) not in self.edges]
        if missing_nodes or task.ontology_gap == "knowledge":
            causes.append(("missing_ontology_knowledge",
                           f"objects absent from the ontology: {missing_nodes}" if missing_nodes
                           else "the ontology deliberately has no knowledge for this question"))
        if missing_edges or task.ontology_gap == "relationship":
            causes.append(("missing_ontology_relationship", f"relationships absent from the ontology: {missing_edges}"))
        if not coverage["covered"]:
            causes.append(("insufficient_tool_coverage", "; ".join(coverage["reasons"])))
        if trace["stop_reason"] == "llm_error":
            causes.append(("backend_execution_error", f"LLM call failed: {trace.get('error')}"))
        if tool_eval["backend_errors"]:
            first = next(c for c in trace["tool_calls"] if not c["ok"] and c["error"]["code"] not in ARGUMENT_ERROR_CODES | SELECTION_ERROR_CODES)
            causes.append(("backend_execution_error", f"{first['tool']}: {first['error']['code']} {first['error']['message']}"))

        if coverage["covered"] and coverage["usable_sequences"] and task.expected.behavior != "abstain":
            recall = tool_eval["required_tool_recall"] or 0
            if recall < 1 or tool_eval["selection_errors"]:
                missing = [t for t in tool_eval["best_sequence"] if t not in tool_eval["called"]]
                causes.append(("incorrect_tool_selection",
                               f"needed tools never called: {missing}" if missing else "called a tool that is not available"))
            elif tool_eval["sequencing_correctness"] is not None and tool_eval["sequencing_correctness"] < 1:
                causes.append(("incorrect_tool_sequence", f"expected order {tool_eval['best_sequence']}, got {tool_eval['called']}"))
            if tool_eval["argument_checks_passed"] not in (None, 1.0) or \
                    (tool_eval["argument_errors"] and evidence["retrieved"] is False):
                causes.append(("incorrect_tool_arguments",
                               f"{tool_eval['argument_errors']} call(s) rejected for bad arguments; "
                               f"argument checks passed: {tool_eval['argument_checks_passed']}"))
            if trace["stop_reason"] in ("max_tool_calls", "timeout") and evidence["retrieved"] is not True:
                causes.append(("incorrect_tool_sequence", f"ran out of budget ({trace['stop_reason']}) before finding the answer"))
            if evidence["retrieved"] is False and recall == 1 and not any(c[0] == "incorrect_tool_arguments" for c in causes) \
                    and not missing_nodes and not missing_edges:
                causes.append(("retrieval_failure",
                               f"right tools called but results lacked: {evidence['missing_ids'] + evidence['missing_values']}"))
        if evidence["retrieved"] is True or (not evidence["defined"] and coverage["covered"]):
            causes.append(("incorrect_result_interpretation",
                           f"the needed evidence was in the tool results; answer missed: {answer_eval['misses']}"
                           if answer_eval["misses"] else
                           (f"the answer named {len(answer_eval['extra_objects'])} objects beyond the expected ones"
                            if answer_eval["over_inclusive"] else
                            f"expected the agent to {answer_eval['expected_behavior']}; forbidden: {answer_eval['forbidden_hits']}")))
        if grounded["hallucinated"] or (not coverage["covered"] and not answer_eval["abstained"]):
            causes.append(("ungrounded_final_answer",
                           f"stated without support in tool results: {grounded['ungrounded_objects'] + grounded['ungrounded_numbers']}"
                           if grounded["hallucinated"] else "answered although the tools could not provide the answer"))
        if not causes:
            causes.append(("incorrect_result_interpretation", "no specific cause identified; read the trace"))

        seen, ordered = set(), []
        for category, detail in causes:
            if category not in seen:
                seen.add(category)
                ordered.append({"category": category, "detail": detail})
        layer = {"missing_ontology_knowledge": "ontology", "missing_ontology_relationship": "ontology",
                 "insufficient_tool_coverage": "access_layer", "backend_execution_error": "access_layer",
                 "retrieval_failure": "access_layer"}.get(ordered[0]["category"], "llm")
        return {"primary": ordered[0]["category"], "layer": layer, "detail": ordered[0]["detail"],
                "contributing": ordered[1:]}


def _lcs(a: list[Any], b: list[Any]) -> int:
    table = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i, x in enumerate(a, 1):
        for j, y in enumerate(b, 1):
            table[i][j] = table[i - 1][j - 1] + 1 if x == y else max(table[i - 1][j], table[i][j - 1])
    return table[-1][-1]
