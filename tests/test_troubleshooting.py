"""The troubleshooting workspace: case tools on the diagram-based ontology, and the agent that uses them."""

import json

import pytest
from fastapi.testclient import TestClient

from ontolab.agents.runtime import AgentRuntime
from ontolab.api import main, troubleshooting as api
from ontolab.llm.base import LLMResponse, ToolCall
from ontolab.llm.mock import MockLLM
from ontolab.ontology.store import OntologyAccessError
from ontolab.troubleshooting.engine import CaseError
from ontolab.troubleshooting.workspace import CONFIG, build_workspace


@pytest.fixture()
def workspace():
    return build_workspace(db_path=":memory:")


def candidates(*pairs):
    return [{"symptom_id": s, "verdict": v, "reason": "test"} for s, v in pairs]


def test_matching_separates_specific_from_vague(workspace):
    engine = workspace.engine
    vague = engine.match_symptoms("My computer screen is dead")
    assert vague["description_quality"] == "vague"
    assert {c["symptom_id"] for c in vague["candidates"]} == {"S_NO_POWER", "S_BLACK_SCREEN", "S_SCREEN_DAMAGE"}
    assert all(c["matched_as"] == "vague" for c in vague["candidates"])
    clear = engine.match_symptoms("It is plugged in but the percentage never goes up")
    assert clear["description_quality"] == "specific" and clear["candidates"][0]["symptom_id"] == "S_NOT_CHARGING"
    assert clear["candidates"][0]["matched_as"] == "typical"
    assert engine.match_symptoms("I want a refund")["description_quality"] == "unrecognised"


def test_next_check_is_the_most_discriminating_one(workspace):
    engine = workspace.engine
    case_id = engine.open_case("My computer screen is dead")["case_id"]
    assert engine.suggest_next_check(case_id)["suggestion"] is None  # nothing assessed yet
    engine.record_assessment(case_id, candidates(("S_NO_POWER", "candidate"), ("S_BLACK_SCREEN", "candidate"), ("S_SCREEN_DAMAGE", "candidate")), "vague wording")
    suggestion = engine.suggest_next_check(case_id)
    assert suggestion["suggestion"]["check_id"] in ("CK_POWER_LED", "CK_SCREEN_PHYSICAL") and suggestion["purpose"] == "narrow the candidates"
    by_result = {o["result"]: o for o in suggestion["if_the_result_is"]}
    assert all(o["supports"] and o["rules_out"] for o in by_result.values()), "each answer both supports and rules out something"
    steps = suggestion["suggestion"]["steps"]
    assert steps[-1]["instruction"].endswith("?") and suggestion["suggestion"]["pending_steps"] == [s["step"] for s in steps]
    assert all(a["steps"] and a["if_the_result_is"] for a in suggestion["alternatives"]), "alternatives are complete enough to choose from"


def test_code_enforces_result_codes_confirmation_and_solutions(workspace):
    engine = workspace.engine
    case_id = engine.open_case("My computer screen is dead", "DEV_LEGION5")["case_id"]
    with pytest.raises(CaseError) as bad_code:
        engine.record_check_result(case_id, "CK_POWER_LED", "on", "the light is on", "stated up front")
    assert bad_code.value.code == "invalid_result_code" and bad_code.value.details["valid_results"]
    engine.record_check_result(case_id, "CK_POWER_LED", "lit", "the light is on", "stated up front")
    with pytest.raises(CaseError) as early:
        engine.record_assessment(case_id, candidates(("S_BLACK_SCREEN", "confirmed")), "guess")
    assert early.value.code == "conditions_not_met" and early.value.details["not_yet_checked"][0]["check"] == "CK_SCREEN_PHYSICAL"
    with pytest.raises(CaseError) as no_symptom:
        engine.applicable_solutions(case_id)
    assert no_symptom.value.code == "no_confirmed_symptom"

    engine.record_check_result(case_id, "CK_SCREEN_PHYSICAL", "intact", "no cracks", "stated up front")
    recorded = engine.record_assessment(case_id, candidates(("S_BLACK_SCREEN", "confirmed"), ("S_NO_POWER", "ruled_out"), ("S_SCREEN_DAMAGE", "ruled_out")), "both conditions met")
    assert recorded["recorded"]["status"] == "confirmed" and recorded["warnings"] == []
    solutions = engine.applicable_solutions(case_id)
    assert solutions["applicable"] == [] and {s["solution_id"] for s in solutions["undetermined"]} >= {"SOL_REPAIR_DISPLAY", "SOL_GPU_DRIVER"}
    # Triage is done, so the next check comes from the "locate the cause" checklist, in step order.
    following = engine.suggest_next_check(case_id)
    assert following["from_checklist"]["role"] == "locate_cause" and following["suggestion"]["check_id"] == "CK_EXTERNAL_MONITOR"
    with pytest.raises(CaseError) as unproven:
        engine.select_solution(case_id, "SOL_REPAIR_DISPLAY")
    assert unproven.value.code == "solution_not_applicable"

    engine.record_check_result(case_id, "CK_EXTERNAL_MONITOR", "external_blank", "TV is blank too", "stated up front")
    assert engine.applicable_solutions(case_id)["applicable"] == [], "the graphics fix also depends on whether the system is running"
    engine.record_check_result(case_id, "CK_CAPS_LOCK", "toggles", "the light goes on and off", "stated up front")
    assert [s["solution_id"] for s in engine.applicable_solutions(case_id)["applicable"]] == ["SOL_GPU_DRIVER"]
    with pytest.raises(CaseError):
        engine.select_solution(case_id, "SOL_REPAIR_DISPLAY")
    closed = engine.select_solution(case_id, "SOL_GPU_DRIVER", "resolved")
    assert closed["status"] == "resolved" and closed["selected_solution"]["solution_id"] == "SOL_GPU_DRIVER"
    with pytest.raises(CaseError) as after:
        engine.record_check_result(case_id, "CK_POWER_LED", "lit", "again", "stated up front")
    assert after.value.code == "case_closed"


def test_assessments_are_append_only_with_an_evidence_chain(workspace):
    engine, store = workspace.engine, workspace.store
    case_id = engine.open_case("There's a problem with my battery, it keeps running out of power")["case_id"]
    engine.record_assessment(case_id, candidates(("S_NOT_CHARGING", "supported"), ("S_BATTERY_DRAIN", "candidate")), "leans to not charging")
    engine.record_check_result(case_id, "CK_CHARGE_ICON", "charging", "it says charging", "stated up front")
    contradicted = engine.record_assessment(case_id, candidates(("S_NOT_CHARGING", "supported"), ("S_BATTERY_DRAIN", "candidate")), "unchanged")
    assert any("contradicts" in w for w in contradicted["warnings"]), "keeping a contradicted candidate is flagged"
    engine.record_check_result(case_id, "CK_RUNTIME", "short", "forty minutes", "stated up front")
    engine.record_assessment(case_id, candidates(("S_NOT_CHARGING", "ruled_out"), ("S_BATTERY_DRAIN", "confirmed")), "revised")

    state = engine.case_state(case_id)
    assert [(a["sequence"], a["status"]) for a in state["assessments"]] == [(1, "superseded"), (2, "superseded"), (3, "confirmed")]
    assert [len(a["based_on"]) for a in state["assessments"]] == [0, 1, 2]
    assert state["confirmed_symptom"]["symptom_id"] == "S_BATTERY_DRAIN"
    history = store.traverse_graph(state["assessments"][-1]["assessment_id"], ["SUPERSEDES"], 4)
    assert len(history["nodes"]) == 2, "earlier assessments are kept and reachable"
    # Everything written conforms to the ontology schema.
    with pytest.raises(OntologyAccessError) as wrong:
        store.add_edge(case_id, "RESULT_OF", "CK_RUNTIME")
    assert wrong.value.code == "schema_violation"
    with pytest.raises(OntologyAccessError):
        store.add_node("X1", "CheckResult", "bad", properties={"result": "short"})  # required properties missing


def test_symptoms_have_ordered_checklists_and_device_specific_ones_are_skipped(workspace):
    engine = workspace.engine
    described = engine.describe_symptom("S_BATTERY_DRAIN")
    assert [(c["role"], c["order"]) for c in described["checklists"]] == [("triage", 1), ("locate_cause", 2), ("deeper", 3), ("entitlement", 4)]
    assert described["checklists"][2]["applies_when"] == "removable_battery == true"

    def run(device):
        case_id = engine.open_case("A full charge only lasts about an hour", device)["case_id"]
        engine.record_assessment(case_id, candidates(("S_BATTERY_DRAIN", "candidate")), "typical expression")
        answers = {"CK_CHARGE_ICON": "charging", "CK_RUNTIME": "short", "CK_BATTERY_HEALTH": "healthy", "CK_BACKGROUND_APPS": "none",
                   "CK_POWER_MODE": "balanced", "CK_BATTERY_RESEAT": "no_change", "CK_BATTERY_CONTACTS": "dirty"}
        asked = []
        for _ in range(12):
            suggestion = engine.suggest_next_check(case_id)
            if not suggestion["suggestion"]:
                return asked, suggestion["reason"], engine.applicable_solutions(case_id)["applicable"]
            check = suggestion["suggestion"]["check_id"]
            asked.append(check)
            engine.record_check_result(case_id, check, answers[check], "customer answer", "stated up front")
            if asked == ["CK_CHARGE_ICON", "CK_RUNTIME"]:
                engine.record_assessment(case_id, candidates(("S_BATTERY_DRAIN", "confirmed")), "conditions met")
            if engine.case_state(case_id)["confirmed_symptom"] and engine.applicable_solutions(case_id)["applicable"]:
                return asked, "solved", engine.applicable_solutions(case_id)["applicable"]

    asked, reason, solutions = run("DEV_T480")  # removable battery: the third checklist applies
    assert asked[:5] == ["CK_CHARGE_ICON", "CK_RUNTIME", "CK_BATTERY_HEALTH", "CK_BACKGROUND_APPS", "CK_POWER_MODE"]
    assert "CK_BATTERY_CONTACTS" in asked and [s["solution_id"] for s in solutions] == ["SOL_CLEAN_BATTERY_CONTACTS"]
    asked, reason, solutions = run("DEV_SLIM5")   # sealed battery: that checklist is skipped
    assert "CK_BATTERY_CONTACTS" not in asked and "CK_BATTERY_RESEAT" not in asked
    assert solutions == [] and "Escalate" in reason


def test_warranty_gates_repairs_and_software_and_warranty_cases_work(workspace):
    engine = workspace.engine

    def confirmed_case(description, symptom, results, device=None):
        case_id = engine.open_case(description, device)["case_id"]
        for check, result in results.items():
            engine.record_check_result(case_id, check, result, "customer answer", "stated up front")
        engine.record_assessment(case_id, candidates((symptom, "confirmed")), "conditions met")
        return case_id

    # Hardware: a worn battery is a covered part in warranty, and a paid quote out of warranty.
    worn = {"CK_CHARGE_ICON": "charging", "CK_RUNTIME": "short", "CK_BATTERY_HEALTH": "degraded"}
    case_id = confirmed_case("A full charge only lasts about an hour", "S_BATTERY_DRAIN", worn, "DEV_SLIM5")
    pending = engine.applicable_solutions(case_id)
    assert pending["applicable"] == [] and {s["solution_id"] for s in pending["undetermined"]} >= {"SOL_REPLACE_BATTERY", "SOL_PAID_REPAIR_QUOTE"}
    asked = engine.suggest_next_check(case_id)
    assert asked["suggestion"]["check_id"] == "CK_WARRANTY_STATUS" and asked["from_checklist"]["role"] == "entitlement"
    with pytest.raises(CaseError):
        engine.select_solution(case_id, "SOL_REPLACE_BATTERY")  # no free part before cover is recorded
    engine.record_check_result(case_id, "CK_WARRANTY_STATUS", "expired", "warranty ended last year", "stated up front")
    assert [s["solution_id"] for s in engine.applicable_solutions(case_id)["applicable"]] == ["SOL_PAID_REPAIR_QUOTE"]
    covered = confirmed_case("A full charge only lasts about an hour", "S_BATTERY_DRAIN", {**worn, "CK_WARRANTY_STATUS": "in_warranty"})
    assert [s["solution_id"] for s in engine.applicable_solutions(covered)["applicable"]] == ["SOL_REPLACE_BATTERY"]

    # A cracked screen is covered only by accidental damage protection.
    cracked = confirmed_case("I dropped it and there is a crack across the screen", "S_SCREEN_DAMAGE",
                             {"CK_SCREEN_PHYSICAL": "damaged", "CK_LIQUID_SIGNS": "none", "CK_WARRANTY_STATUS": "in_warranty"})
    assert engine.applicable_solutions(cracked)["applicable"] == []
    engine.record_check_result(cracked, "CK_ADP_COVER", "not_covered", "no accidental damage cover", "stated up front")
    assert [s["solution_id"] for s in engine.applicable_solutions(cracked)["applicable"]] == ["SOL_PAID_REPAIR_QUOTE"]

    # Software: a remote fix needs no warranty at all.
    assert engine.match_symptoms("It gets stuck on the Lenovo logo with the spinning circle")["candidates"][0]["symptom_id"] == "S_OS_WONT_START"
    stuck = confirmed_case("It gets stuck on the Lenovo logo", "S_OS_WONT_START",
                           {"CK_POWER_LED": "lit", "CK_BOOT_COMPLETES": "stuck_or_error", "CK_RECENT_CHANGE": "windows_update", "CK_SAFE_MODE": "starts"})
    assert [s["solution_id"] for s in engine.applicable_solutions(stuck)["applicable"]] == ["SOL_UNINSTALL_UPDATE"]

    # Warranty: a question, confirmed without checks, answered from the lookup.
    question = engine.match_symptoms("Is my laptop still under warranty?")
    assert question["description_quality"] == "specific" and question["candidates"][0]["symptom_id"] == "S_WARRANTY_INQUIRY"
    inquiry = confirmed_case("Is my laptop still under warranty?", "S_WARRANTY_INQUIRY", {})
    assert engine.suggest_next_check(inquiry)["suggestion"]["check_id"] == "CK_WARRANTY_STATUS"
    engine.record_check_result(inquiry, "CK_WARRANTY_STATUS", "expired", "ended in 2024", "stated up front")
    assert [s["solution_id"] for s in engine.applicable_solutions(inquiry)["applicable"]] == ["SOL_OFFER_EXTENSION"]


def test_customer_is_led_through_a_check_step_by_step_in_the_order_the_agent_chooses(workspace):
    engine = workspace.engine
    case_id = engine.open_case("It won't charge")["case_id"]
    check = "CK_POWER_RESET"  # four steps
    with pytest.raises(CaseError) as exc:
        engine.record_check_result(case_id, check, "fixed", "works now")
    assert exc.value.code == "steps_incomplete" and exc.value.details["pending_steps"] == [1, 2, 3, 4]

    # The agent takes step 2 before step 1: any order is accepted, and each reply is stored on the case at once.
    after = engine.record_step_feedback(case_id, check, 2, "ok, held it for thirty seconds")
    assert after["pending_steps"] == [1, 3, 4] and "possible_results" not in after
    progress = engine.case_state(case_id)["check_in_progress"]
    assert progress["check_id"] == check and [f["step"] for f in progress["feedback"]] == [2]
    assert engine.suggest_next_check(case_id)["purpose"] == "finish the check in progress"
    engine.record_step_feedback(case_id, check, 1, "unplugged everything")
    with pytest.raises(CaseError) as exc:
        engine.record_step_feedback(case_id, check, 9, "?")
    assert exc.value.code == "unknown_step"
    engine.record_step_feedback(case_id, check, 3, "charger is back in")
    done = engine.record_step_feedback(case_id, check, 4, "yes it starts now")
    assert done["pending_steps"] == [] and {r["code"] for r in done["possible_results"]} == {"fixed", "no_change"}

    engine.record_check_result(case_id, check, "fixed", "yes it starts now")
    state = engine.case_state(case_id)
    stored = state["check_results"][0]["step_feedback"]
    assert [f["step"] for f in stored] == [2, 1, 3, 4] and all(f["feedback"] for f in stored), "kept in the order taken"
    assert state["check_in_progress"] is None

    # Steps the conversation made unnecessary are stored as skipped, with the reason.
    engine.record_step_feedback(case_id, "CK_POWER_LED", 1, "the charger is already in")
    engine.record_check_result(case_id, "CK_POWER_LED", "lit", "the light is on", "customer said the light is on while doing step 1")
    led = engine.case_state(case_id)["check_results"][1]["step_feedback"]
    assert [(f["step"], f["feedback"] is None) for f in led] == [(1, False), (2, True), (3, True)] and led[1]["skipped_because"]

    # Every check definition in the file has spoken steps, one instruction each.
    for definition in workspace.store.all_nodes("CheckDefinition"):
        steps = definition["properties"]["steps"]
        assert steps and all(isinstance(s, str) and len(s) < 200 for s in steps), definition["node_id"]


def test_escalation_and_reset(workspace):
    engine = workspace.engine
    case_id = engine.open_case("I want a refund")["case_id"]
    handover = engine.escalate(case_id, "not a technical problem")
    assert handover["escalated"] and handover["handover_summary"]["raw_description"] == "I want a refund"
    assert engine.case_state(case_id)["status"] == "escalated"
    assert len(engine.list_cases()) == 6
    workspace.reset()
    assert [c["case_id"] for c in engine.list_cases()] == ["CASE_005", "CASE_004", "CASE_003", "CASE_002", "CASE_001"]


class Scripted:
    provider, model, is_mock = "scripted", "scripted-1", False

    def __init__(self, turns):
        self.turns, self.requests = list(turns), []

    def complete(self, system, messages, tools):
        self.requests.append({"system": system, "tools": [t["name"] for t in tools], "messages": len(messages)})
        return self.turns.pop(0)


def call(name, arguments, call_id):
    return LLMResponse(text="", tool_calls=[ToolCall(call_id, name, arguments, json.dumps(arguments))], input_tokens=5, output_tokens=5)


def test_agent_turn_writes_to_the_case_through_tools(workspace):
    case_id = workspace.engine.open_case("My computer screen is dead")["case_id"]
    llm = Scripted([
        call("match_symptoms", {"description": "My computer screen is dead"}, "c1"),
        call("record_assessment", {"case_id": case_id, "evidence": "vague wording", "candidates": candidates(
            ("S_NO_POWER", "candidate"), ("S_BLACK_SCREEN", "candidate"), ("S_SCREEN_DAMAGE", "candidate"))}, "c2"),
        call("suggest_next_check", {"case_id": case_id}, "c3"),
        LLMResponse(text="Please press the power button once. Does the power light come on?", input_tokens=5, output_tokens=5),
    ])
    trace = AgentRuntime(workspace, llm).run("My computer screen is dead", CONFIG.key, config=CONFIG, system=workspace.system_prompt(case_id))
    assert [c["tool"] for c in trace["tool_calls"]] == ["match_symptoms", "record_assessment", "suggest_next_check"]
    assert all(c["ok"] for c in trace["tool_calls"]) and trace["final_answer"].endswith("?")
    assert llm.requests[0]["tools"] == list(CONFIG.tools) and case_id in llm.requests[0]["system"]
    assert len(workspace.engine.case_state(case_id)["current_candidates"]) == 3


def test_api_opens_a_case_and_keeps_the_conversation(services):
    main.install(services, MockLLM())
    api.install(build_workspace(services.settings, db_path=":memory:"), MockLLM())
    with TestClient(main.app) as client:
        counts = client.get("/troubleshooting/status").json()["counts"]
        assert (counts["Symptom"], counts["Checklist"], counts["CheckDefinition"]) == (8, 23, 39)
        first = client.post("/troubleshooting/chat", json={"message": "My computer screen is dead", "device_id": "DEV_SLIM5"}).json()
        assert first["case_id"] == "CASE_006" and first["case"]["raw_description"] == "My computer screen is dead"
        assert first["case"]["device"]["device_id"] == "DEV_SLIM5" and first["trace"]["tools_available"] == list(CONFIG.tools)
        second = client.post("/troubleshooting/chat", json={"message": "the light is on", "conversation_id": first["conversation_id"]}).json()
        assert second["case_id"] == "CASE_006" and second["trace"]["conversation"]["turn"] == 2
        assert len(client.get("/troubleshooting/cases").json()["cases"]) == 6
        assert client.get("/troubleshooting/cases/CASE_001").json()["confirmed_symptom"]["symptom_id"] == "S_BLACK_SCREEN"
        assert client.get("/troubleshooting/cases/NOPE").status_code == 404
        assert client.post("/troubleshooting/chat", json={"message": "x", "conversation_id": "nope"}).status_code == 404
        # The Explorer can read either ontology.
        assert len(client.get("/ontology/schema", params={"workspace": "troubleshooting"}).json()["object_types"]) == 8
        assert len(client.get("/ontology/schema").json()["object_types"]) == 13
        graph = client.get("/ontology/graph", params={"workspace": "troubleshooting", "focus": "CASE_001", "depth": 1}).json()
        assert {n["node_type"] for n in graph["nodes"]} >= {"SupportCase", "SymptomAssessment", "CheckResult", "Solution", "Device"}
        assert client.get("/ontology/schema", params={"workspace": "nope"}).status_code == 400
        assert client.post("/troubleshooting/reset").json()["status"]["counts"]["SupportCase"] == 5
