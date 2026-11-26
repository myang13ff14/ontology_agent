"""Case logic for the diagram-based troubleshooting ontology.

Everything a troubleshooting conversation produces is written back into the
graph, in the shape the ontology defines:

    SupportCase --HAS_ASSESSMENT--> SymptomAssessment --ASSESSES--> Symptom
                                    SymptomAssessment --BASED_ON--> CheckResult
                                    SymptomAssessment --SUPERSEDES--> earlier assessment
    SupportCase --HAS_CHECK_RESULT--> CheckResult --RESULT_OF--> CheckDefinition
    SupportCase --SELECTED_SOLUTION--> Solution

Three decisions are made by code here, not by the model:
  * a check result must use a result code its check definition lists;
  * a symptom can be marked confirmed only when every one of its distinguishing
    conditions is met by recorded check results;
  * a solution can be selected only when its condition holds for those results.
"""

from __future__ import annotations

import threading
from datetime import datetime

from ..errors import ToolError
from ..ontology import rules
from ..ontology.store import OntologyStore
from ..text import overlap, tokens

SYMPTOM, CHECK, CHECKLIST, SOLUTION, CASE, ASSESSMENT, RESULT, DEVICE = (
    "Symptom", "CheckDefinition", "Checklist", "Solution", "SupportCase", "SymptomAssessment", "CheckResult", "Device")
VERDICTS = ("candidate", "supported", "ruled_out", "confirmed")
LIVE = ("candidate", "supported", "confirmed")
MATCH_THRESHOLD = 0.5


class CaseError(ToolError):
    pass


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _similarity(description: str, expression: str) -> float:
    """How well a customer description matches one stored expression (0..1)."""
    a, b = set(tokens(description)), set(tokens(expression))
    if not a or not b:
        return 0.0
    dice = 2 * len(a & b) / (len(a) + len(b))
    contained = 0.9 * overlap(description, expression) if len(b) >= 2 else 0.0  # the whole expression appears in the description
    return round(max(dice, contained), 3)


class TroubleshootingEngine:
    def __init__(self, store: OntologyStore):
        self.store = store
        self._lock = threading.RLock()

    # ------------------------------------------------------------ knowledge

    def _require(self, object_id: str, object_type: str) -> dict:
        node = self.store.get_object(object_id)
        if node["node_type"] != object_type:
            raise CaseError("wrong_object_type", f"'{object_id}' is a {node['node_type']}, not a {object_type}.",
                            expected=object_type, actual=node["node_type"])
        return node

    def _out(self, node_id: str, relationship: str) -> list[dict]:
        return self.store.get_neighbors(node_id, relationship, "out", 200)["neighbors"]

    def _check_brief(self, check: dict) -> dict:
        return {"check_id": check["node_id"], "name": check["canonical_name"], "question": check["properties"]["question"],
                "method": check["properties"].get("method"), "possible_results": check["properties"]["possible_results"],
                "safety_note": check["properties"].get("safety_note")}

    def list_symptoms(self) -> dict:
        symptoms = self.store.all_nodes(SYMPTOM)
        return {"symptoms": [{"symptom_id": s["node_id"], "name": s["canonical_name"], "definition": s["definition"],
                              "typical_expressions": s["properties"]["typical_expressions"]} for s in symptoms],
                "provenance": {"node_ids": [s["node_id"] for s in symptoms], "edge_ids": []}}

    def match_symptoms(self, description: str) -> dict:
        """Map the customer's own words onto standard symptoms, saying whether the wording is specific or vague."""
        candidates = []
        for symptom in self.store.all_nodes(SYMPTOM):
            properties = symptom["properties"]
            groups = {"name": [symptom["canonical_name"]] + symptom["synonyms"],
                      "typical": properties["typical_expressions"], "vague": properties.get("vague_expressions", [])}
            best = max(((_similarity(description, expression), kind, expression)
                        for kind, expressions in groups.items() for expression in expressions),
                       key=lambda item: (item[0], item[1] != "vague"))
            if best[0] >= MATCH_THRESHOLD:
                candidates.append({"symptom_id": symptom["node_id"], "name": symptom["canonical_name"], "score": best[0],
                                   "matched_as": best[1], "matched_expression": best[2], "definition": symptom["definition"]})
        candidates.sort(key=lambda c: (-c["score"], c["matched_as"] == "vague", c["symptom_id"]))
        if not candidates:
            quality = "unrecognised"
        else:
            top, second = candidates[0], candidates[1] if len(candidates) > 1 else None
            clear = top["matched_as"] != "vague" and top["score"] >= 0.6 and (second is None or top["score"] - second["score"] >= 0.2)
            quality = "specific" if clear else "vague"
        return {
            "description": description, "description_quality": quality, "candidates": candidates,
            "guidance": {"specific": "One symptom fits clearly. Still confirm it with its distinguishing conditions.",
                         "vague": "Several symptoms fit, or the wording is a known vague expression. Keep all candidates and narrow them with checks.",
                         "unrecognised": "No stored expression matches. Use list_symptoms and ask the customer what they observe."}[quality],
            "provenance": {"node_ids": [c["symptom_id"] for c in candidates], "edge_ids": []},
        }

    def _checklist(self, symptom_id: str, device: dict | None = None) -> tuple[list[dict], list[str], list[str]]:
        """The symptom's checklists in working order, each with its ordered checks.

        A checklist with an `applies_when` condition is evaluated against the device's
        properties: it is left out when the condition is false, and kept (flagged as
        unconfirmed) when the device is unknown.
        """
        steps, node_ids, edge_ids = [], [], []
        links = sorted(self._out(symptom_id, "HAS_CHECKLIST"), key=lambda n: (n["edge_properties"].get("order", 99), n["node_id"]))
        for checklist in links:
            condition = checklist["edge_properties"].get("applies_when")
            applies = True
            if condition:
                applies = rules.evaluate(condition, device or {})["satisfied"]
                if applies is False:
                    continue
            node_ids.append(checklist["node_id"])
            edge_ids.append(checklist["edge_id"])
            contained = sorted(self._out(checklist["node_id"], "CONTAINS_CHECK"), key=lambda n: n["edge_properties"].get("step_order", 0))
            for item in contained:
                steps.append({"checklist_id": checklist["node_id"], "checklist": checklist["canonical_name"],
                              "role": checklist["edge_properties"].get("role"), "order": checklist["edge_properties"].get("order"),
                              "applies_when": condition, "applies": applies,
                              "step": item["edge_properties"].get("step_order"), "check_id": item["node_id"], "name": item["canonical_name"]})
                node_ids.append(item["node_id"])
                edge_ids.append(item["edge_id"])
        return steps, node_ids, edge_ids

    def _device_properties(self, case_id: str) -> dict | None:
        device = self._out(case_id, "INVOLVES_DEVICE")
        return self.store.get_object(device[0]["node_id"])["properties"] if device else None

    def describe_symptom(self, symptom_id: str) -> dict:
        symptom = self._require(symptom_id, SYMPTOM)
        steps, node_ids, edge_ids = self._checklist(symptom_id)
        conditions = []
        for condition in symptom["properties"]["distinguishing_conditions"]:
            check = self._require(condition["check"], CHECK)
            conditions.append({**condition, "check_name": check["canonical_name"], "question": check["properties"]["question"]})
        return {
            "symptom_id": symptom_id, "name": symptom["canonical_name"], "definition": symptom["definition"],
            "severity": symptom["properties"].get("severity"), "typical_expressions": symptom["properties"]["typical_expressions"],
            "vague_expressions": symptom["properties"].get("vague_expressions", []),
            "distinguishing_conditions": conditions,
            "checklists": [{"checklist_id": cid, "name": group[0]["checklist"], "role": group[0]["role"], "order": group[0]["order"],
                            "applies_when": group[0]["applies_when"],
                            "checks": [{k: g[k] for k in ("step", "check_id", "name")} for g in group]}
                           for cid in dict.fromkeys(st["checklist_id"] for st in steps)
                           for group in [[st for st in steps if st["checklist_id"] == cid]]],
            "provenance": {"node_ids": [symptom_id] + node_ids, "edge_ids": edge_ids},
        }

    # ------------------------------------------------------------- case state

    def _case(self, case_id: str) -> dict:
        try:
            return self._require(case_id, CASE)
        except ToolError as exc:
            if exc.code == "object_not_found":
                raise CaseError("unknown_case", f"There is no case '{case_id}'.") from None
            raise

    def _results(self, case_id: str) -> list[dict]:
        out = []
        for item in self._out(case_id, "HAS_CHECK_RESULT"):
            node = self.store.get_object(item["node_id"])
            definition = self._out(item["node_id"], "RESULT_OF")[0]
            out.append({"result_id": node["node_id"], "check_id": definition["node_id"], "check": definition["canonical_name"],
                        **node["properties"], "_edges": [item["edge_id"], definition["edge_id"]]})
        return sorted(out, key=lambda r: (r["executed_at"], r["result_id"]))

    def _assessments(self, case_id: str) -> list[dict]:
        out = []
        for item in self._out(case_id, "HAS_ASSESSMENT"):
            node = self.store.get_object(item["node_id"])
            assessed = self._out(item["node_id"], "ASSESSES")
            out.append({
                "assessment_id": node["node_id"], **node["properties"],
                "candidates": [{"symptom_id": a["node_id"], "name": a["canonical_name"], **a["edge_properties"]} for a in assessed],
                "based_on": [b["node_id"] for b in self._out(item["node_id"], "BASED_ON")],
                "supersedes": [s["node_id"] for s in self._out(item["node_id"], "SUPERSEDES")],
                "_edges": [item["edge_id"]] + [a["edge_id"] for a in assessed],
            })
        return sorted(out, key=lambda a: a["sequence"])

    @staticmethod
    def _facts(results: list[dict]) -> dict[str, str]:
        """check id -> latest recorded result code."""
        return {r["check_id"]: r["result"] for r in results}

    def _conditions(self, symptom_id: str, facts: dict[str, str]) -> dict:
        symptom = self.store.get_object(symptom_id)
        met, unmet, contradicted = [], [], []
        for condition in symptom["properties"]["distinguishing_conditions"]:
            actual = facts.get(condition["check"])
            entry = {"check": condition["check"], "expect": condition["expect"], "actual": actual}
            (met if actual == condition["expect"] else contradicted if actual is not None else unmet).append(entry)
        return {"met": met, "not_yet_checked": unmet, "contradicted": contradicted}

    def case_state(self, case_id: str) -> dict:
        case = self._case(case_id)
        results, assessments = self._results(case_id), self._assessments(case_id)
        facts = self._facts(results)
        latest = assessments[-1] if assessments else None
        live = [c for c in latest["candidates"] if c.get("verdict") in LIVE] if latest else []
        confirmed = [c for c in live if c.get("verdict") == "confirmed"]
        device = self._out(case_id, "INVOLVES_DEVICE")
        solution = self._out(case_id, "SELECTED_SOLUTION")
        node_ids = [case_id] + [r["result_id"] for r in results] + [r["check_id"] for r in results] \
            + [a["assessment_id"] for a in assessments] + [c["symptom_id"] for a in assessments for c in a["candidates"]]
        edge_ids = [e for item in results + assessments for e in item["_edges"]]
        strip = lambda item: {k: v for k, v in item.items() if not k.startswith("_")}  # noqa: E731
        return {
            "case_id": case_id, "name": case["canonical_name"], **case["properties"],
            "device": {"device_id": device[0]["node_id"], "name": device[0]["canonical_name"]} if device else None,
            "current_candidates": [{**c, "conditions": self._conditions(c["symptom_id"], facts)} for c in live],
            "confirmed_symptom": confirmed[0] if confirmed else None,
            "assessments": [strip(a) for a in assessments], "check_results": [strip(r) for r in results],
            "selected_solution": ({"solution_id": solution[0]["node_id"], "name": solution[0]["canonical_name"],
                                   **solution[0]["edge_properties"]} if solution else None),
            "provenance": {"node_ids": list(dict.fromkeys(node_ids)), "edge_ids": edge_ids},
        }

    def list_cases(self) -> list[dict]:
        cases = self.store.all_nodes(CASE)
        return [{"case_id": c["node_id"], "name": c["canonical_name"], "source": c["source"], **c["properties"]}
                for c in sorted(cases, key=lambda c: c["properties"].get("opened_at") or "", reverse=True)]

    # ---------------------------------------------------------------- writes

    def _open(self, case_id: str) -> dict:
        case = self._case(case_id)
        if case["properties"]["status"] != "in_progress":
            raise CaseError("case_closed", f"Case {case_id} is {case['properties']['status']} and can no longer be changed.")
        return case

    def open_case(self, raw_description: str, device_id: str | None = None, channel: str = "chat") -> dict:
        with self._lock:
            if device_id:
                self._require(device_id, DEVICE)
            number = 1 + max((int(c["node_id"].split("_")[1]) for c in self.store.all_nodes(CASE)
                              if c["node_id"].split("_")[1].isdigit()), default=0)
            case_id = f"CASE_{number:03d}"
            summary = raw_description.strip().splitlines()[0][:60]
            self.store.add_node(case_id, CASE, f"Case {number:03d}: {summary}", "Opened in the lab.", properties={
                "raw_description": raw_description, "status": "in_progress", "channel": channel, "opened_at": _now()})
            if device_id:
                self.store.add_edge(case_id, "INVOLVES_DEVICE", device_id)
            return self.case_state(case_id)

    def record_check_result(self, case_id: str, check_id: str, result: str, evidence: str) -> dict:
        with self._lock:
            self._open(case_id)
            check = self._require(check_id, CHECK)
            valid = [r["code"] for r in check["properties"]["possible_results"]]
            if result not in valid:
                raise CaseError("invalid_result_code", f"'{result}' is not a result of {check['canonical_name']}.",
                                valid_results=check["properties"]["possible_results"])
            number = case_id.split("_")[1]
            result_id = f"CR_{number}_{len(self._results(case_id)) + 1}"
            self.store.add_node(result_id, RESULT, f"Case {number} check: {check['canonical_name']}",
                                properties={"result": result, "evidence": evidence, "executed_at": _now()})
            self.store.add_edge(case_id, "HAS_CHECK_RESULT", result_id)
            self.store.add_edge(result_id, "RESULT_OF", check_id)
            state = self.case_state(case_id)
            label = next(r["label"] for r in check["properties"]["possible_results"] if r["code"] == result)
            return {"recorded": {"result_id": result_id, "check_id": check_id, "result": result, "meaning": label},
                    "effect_on_candidates": [{"symptom_id": c["symptom_id"], "name": c["name"], **c["conditions"]}
                                             for c in state["current_candidates"]],
                    "next": "Record a new assessment that reflects this result.", "provenance": state["provenance"]}

    def record_assessment(self, case_id: str, candidates: list[dict], evidence: str) -> dict:
        """Append a judgement. It supersedes the previous one, which is kept."""
        with self._lock:
            self._open(case_id)
            if not candidates:
                raise CaseError("invalid_assessment", "An assessment must evaluate at least one candidate symptom.")
            results = self._results(case_id)
            facts = self._facts(results)
            warnings = []
            for candidate in candidates:
                self._require(candidate["symptom_id"], SYMPTOM)
                if candidate["verdict"] not in VERDICTS:
                    raise CaseError("invalid_assessment", f"Unknown verdict '{candidate['verdict']}'.", valid=list(VERDICTS))
                conditions = self._conditions(candidate["symptom_id"], facts)
                if candidate["verdict"] == "confirmed" and (conditions["not_yet_checked"] or conditions["contradicted"]):
                    raise CaseError(
                        "conditions_not_met",
                        f"{candidate['symptom_id']} cannot be confirmed: its distinguishing conditions are not all met by recorded check results.",
                        **conditions)
                if candidate["verdict"] == "ruled_out" and not conditions["contradicted"]:
                    warnings.append(f"{candidate['symptom_id']} was ruled out although no recorded check result contradicts it.")
                if candidate["verdict"] in ("candidate", "supported") and conditions["contradicted"]:
                    warnings.append(f"{candidate['symptom_id']} is kept although a recorded check result contradicts it: {conditions['contradicted']}")
            if sum(c["verdict"] == "confirmed" for c in candidates) > 1:
                raise CaseError("invalid_assessment", "Only one symptom can be confirmed.")

            previous = self._assessments(case_id)
            live_now = [c for c in candidates if c["verdict"] in LIVE]
            live_before = [c for c in previous[-1]["candidates"] if c.get("verdict") in LIVE] if previous else []
            if any(c["verdict"] == "confirmed" for c in candidates):
                status = "confirmed"
            elif previous and len(live_now) < len(live_before):
                status = "narrowed"
            else:
                status = "candidates"
            number, sequence = case_id.split("_")[1], len(previous) + 1
            assessment_id = f"AS_{number}_{sequence}"
            self.store.add_node(assessment_id, ASSESSMENT, f"Case {number} assessment {sequence}",
                                properties={"sequence": sequence, "status": status, "evidence": evidence, "assessed_at": _now()})
            self.store.add_edge(case_id, "HAS_ASSESSMENT", assessment_id)
            for candidate in candidates:
                self.store.add_edge(assessment_id, "ASSESSES", candidate["symptom_id"],
                                    {"verdict": candidate["verdict"], "reason": candidate.get("reason", "")})
            for result in results:  # the judgement rests on everything known so far
                self.store.add_edge(assessment_id, "BASED_ON", result["result_id"])
            if previous:
                self.store.add_edge(assessment_id, "SUPERSEDES", previous[-1]["assessment_id"])
                self.store.update_properties(previous[-1]["assessment_id"], {"status": "superseded"})
            state = self.case_state(case_id)
            return {"recorded": {"assessment_id": assessment_id, "sequence": sequence, "status": status,
                                 "supersedes": previous[-1]["assessment_id"] if previous else None,
                                 "based_on": [r["result_id"] for r in results]},
                    "warnings": warnings, "current_candidates": state["current_candidates"],
                    "confirmed_symptom": state["confirmed_symptom"], "provenance": state["provenance"]}

    # ------------------------------------------------------------- reasoning

    def suggest_next_check(self, case_id: str) -> dict:
        """The check that would tell the most, given the current candidates and what is already known."""
        state = self.case_state(case_id)
        facts = self._facts(state["check_results"])
        live = state["current_candidates"]
        if not live:
            return {"suggestion": None, "reason": "There is no assessment with live candidates yet. Call match_symptoms and record_assessment first.",
                    "provenance": state["provenance"]}

        def implications(check_id: str) -> list[dict]:
            check = self.store.get_object(check_id)
            out = []
            for option in check["properties"]["possible_results"]:
                supports, rules_out = [], []
                for candidate in live:
                    for condition in self.store.get_object(candidate["symptom_id"])["properties"]["distinguishing_conditions"]:
                        if condition["check"] == check_id:
                            (supports if condition["expect"] == option["code"] else rules_out).append(candidate["symptom_id"])
                out.append({"result": option["code"], "meaning": option["label"], "supports": supports, "rules_out": rules_out})
            return out

        confirmed = state["confirmed_symptom"]
        if confirmed:
            solutions = self.applicable_solutions(case_id)
            needed = list(dict.fromkeys(check for s in solutions["undetermined"] for check in s["missing_checks"]))
            if solutions["applicable"] or not needed:
                return {"suggestion": None, "reason": "The symptom is confirmed and no further check is needed. Call get_applicable_solutions.",
                        "provenance": state["provenance"]}
            # Work the symptom's checklists in order, skipping any that do not apply to this device.
            steps = self._checklist(confirmed["symptom_id"], self._device_properties(case_id))[0]
            position = {}
            for index, step in enumerate(steps):
                position.setdefault(step["check_id"], (index, step))
            needed = sorted((c for c in needed if c in position), key=lambda c: position[c][0])
            if not needed:
                return {"suggestion": None, "reason": "No remaining check applies to this device and no solution is established. Escalate the case.",
                        "provenance": state["provenance"]}
            step = position[needed[0]][1]
            check = self.store.get_object(needed[0])
            return {"purpose": "choose the solution", "suggestion": self._check_brief(check),
                    "from_checklist": {k: step[k] for k in ("checklist_id", "checklist", "role", "step")},
                    "reason": f"{confirmed['name']} is confirmed. This is the next check on its '{step['checklist']}' checklist that decides which solution applies.",
                    "also_needed": needed[1:], "provenance": state["provenance"]}

        ranked = []
        pending = {c["check"] for candidate in live for c in candidate["conditions"]["not_yet_checked"]}
        for check_id in sorted(pending):
            options = implications(check_id)
            eliminated = sum(len(o["rules_out"]) for o in options) / len(options)
            touched = len({s for o in options for s in o["supports"] + o["rules_out"]})
            ranked.append((eliminated, touched, check_id, options))
        ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
        if not ranked:
            return {"suggestion": None, "reason": "Every distinguishing condition of the live candidates has been checked. Record an assessment.",
                    "provenance": state["provenance"]}
        eliminated, _, check_id, options = ranked[0]
        return {
            "purpose": "narrow the candidates" if len(live) > 1 else "confirm the candidate",
            "suggestion": self._check_brief(self.store.get_object(check_id)), "if_the_result_is": options,
            "reason": f"Of the checks not yet done, this one rules out the most candidates on average ({eliminated:.1f} of {len(live)}).",
            "alternatives": [{"check_id": c, "expected_eliminations": round(e, 2)} for e, _, c, _ in ranked[1:4]],
            "provenance": state["provenance"],
        }

    def applicable_solutions(self, case_id: str) -> dict:
        state = self.case_state(case_id)
        confirmed = state["confirmed_symptom"]
        if not confirmed:
            raise CaseError("no_confirmed_symptom", "No symptom is confirmed for this case yet, so no solution can be chosen.",
                            current_candidates=[c["symptom_id"] for c in state["current_candidates"]])
        facts = self._facts(state["check_results"])
        applicable, undetermined, not_applicable = [], [], []
        links = self._out(confirmed["symptom_id"], "RESOLVED_BY")
        for link in sorted(links, key=lambda n: (n["edge_properties"].get("priority", 99), n["node_id"])):
            solution = self.store.get_object(link["node_id"])
            condition = link["edge_properties"].get("condition")
            entry = {"solution_id": solution["node_id"], "name": solution["canonical_name"], "definition": solution["definition"],
                     "condition": condition, "priority": link["edge_properties"].get("priority"), **solution["properties"]}
            verdict = rules.evaluate(condition, facts) if condition else {"satisfied": True, "missing_context": []}
            if verdict["satisfied"] is True:
                applicable.append(entry)
            elif verdict["satisfied"] is None:
                undetermined.append({**entry, "missing_checks": verdict["missing_context"]})
            else:
                not_applicable.append({k: entry[k] for k in ("solution_id", "name", "condition")})
        return {
            "confirmed_symptom": confirmed, "known_results": facts, "applicable": applicable, "undetermined": undetermined,
            "not_applicable": not_applicable,
            "guidance": ("Offer the first applicable solution." if applicable else
                         "No solution is established yet. Run the missing checks." if undetermined else
                         "No stored solution fits these results. Escalate the case."),
            "provenance": {"node_ids": [confirmed["symptom_id"]] + [n["node_id"] for n in links], "edge_ids": [n["edge_id"] for n in links]},
        }

    def select_solution(self, case_id: str, solution_id: str, outcome: str = "awaiting_customer") -> dict:
        with self._lock:
            self._open(case_id)
            self._require(solution_id, SOLUTION)
            if outcome not in ("resolved", "not_resolved", "awaiting_customer"):
                raise CaseError("invalid_outcome", f"Unknown outcome '{outcome}'.", valid=["resolved", "not_resolved", "awaiting_customer"])
            solutions = self.applicable_solutions(case_id)
            if solution_id not in [s["solution_id"] for s in solutions["applicable"]]:
                raise CaseError("solution_not_applicable", f"{solution_id} is not established as applicable for this case.",
                                applicable=[s["solution_id"] for s in solutions["applicable"]],
                                undetermined=[{"solution_id": s["solution_id"], "missing_checks": s["missing_checks"]} for s in solutions["undetermined"]])
            if self._out(case_id, "SELECTED_SOLUTION"):
                raise CaseError("solution_already_selected", "A solution has already been selected for this case.")
            self.store.add_edge(case_id, "SELECTED_SOLUTION", solution_id, {"outcome": outcome})
            if outcome == "resolved":
                self.store.update_properties(case_id, {"status": "resolved", "closed_at": _now()})
            return self.case_state(case_id)

    def escalate(self, case_id: str, reason: str) -> dict:
        with self._lock:
            self._open(case_id)
            self.store.update_properties(case_id, {"status": "escalated", "closed_at": _now()})
            state = self.case_state(case_id)
            return {"escalated": True, "reason": reason, "handover_summary": {
                "raw_description": state["raw_description"], "confirmed_symptom": state["confirmed_symptom"],
                "current_candidates": [c["name"] for c in state["current_candidates"]],
                "check_results": [{"check": r["check"], "result": r["result"], "evidence": r["evidence"]} for r in state["check_results"]]},
                "provenance": state["provenance"]}
