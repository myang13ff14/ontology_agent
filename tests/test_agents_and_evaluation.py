import json

import pytest

from ontolab.agents.configs import CONFIGS, system_prompt
from ontolab.agents.runtime import AgentRuntime
from ontolab.evaluation.benchmark import CATEGORIES
from ontolab.evaluation.scoring import FAILURE_CATEGORIES, Scorer, number_matches, numbers_in
from ontolab.llm.base import LLMError, LLMResponse, ToolCall


class Scripted:
    """Plays back prepared model turns and records what the runtime sent."""
    provider, model, is_mock = "scripted", "scripted-1", False

    def __init__(self, turns):
        self.turns, self.requests = list(turns), []

    def complete(self, system, messages, tools):
        self.requests.append({"system": system, "messages": [dict(m) for m in messages], "tools": [t["name"] for t in tools]})
        turn = self.turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        return turn


def call(name, arguments, call_id="c1"):
    return LLMResponse(text="", tool_calls=[ToolCall(call_id, name, arguments, json.dumps(arguments))], input_tokens=10, output_tokens=5)


def say(text):
    return LLMResponse(text=text, input_tokens=10, output_tokens=5)


def test_configurations_differ_only_in_tools(services):
    assert list(CONFIGS) == ["A", "B", "C", "D", "E"]
    assert CONFIGS["A"].tools == ("query_semantic_view",)
    assert len(CONFIGS["B"].tools) == 10 and len(CONFIGS["C"].tools) == 10
    assert set(CONFIGS["D"].tools) == set(CONFIGS["E"].tools) == {"search_ontology_profiles", "query_semantic_view"}
    assert not (set(CONFIGS["B"].tools) & set(CONFIGS["C"].tools)) - {"query_semantic_view"}
    for config in CONFIGS.values():
        assert all(services.registry.has(t) for t in config.tools)
    text = services.terminology.render_for_prompt()
    assert "won't take a charge" in system_prompt(CONFIGS["E"], text)
    assert "won't take a charge" not in system_prompt(CONFIGS["D"], text)


def test_prompts_contain_no_benchmark_answers(services, evaluation):
    prompts = " ".join(system_prompt(c, "") for c in CONFIGS.values())
    for task in evaluation.tasks:
        assert task.question not in prompts
    for node_id in ("A_EXTERNAL_MONITOR_TEST", "KA_1044", "CL_WIFI"):
        assert node_id not in prompts


def test_loop_validates_executes_and_feeds_results_back(services):
    llm = Scripted([
        call("resolve_symptom", {"term": "not charging"}),
        call("get_diagnostic_checklist", {"symptom": "S_NOT_CHARGING"}, "c2"),      # wrong argument name
        call("get_diagnostic_checklist", {"symptom_id": "S_NOT_CHARGING"}, "c3"),
        call("get_schema", {}, "c4"),                                                # not offered to agent B
        say("Use the Not Charging Checklist (CL_NOT_CHARGING)."),
    ])
    trace = AgentRuntime(services, llm).run("Which checklist for not charging?", "B")
    assert trace["final_answer"].startswith("Use the Not Charging Checklist") and trace["stop_reason"] == "answered"
    assert [(c["tool"], c["ok"]) for c in trace["tool_calls"]] == [
        ("resolve_symptom", True), ("get_diagnostic_checklist", False), ("get_diagnostic_checklist", True), ("get_schema", False)]
    assert trace["tool_calls"][1]["error"]["code"] == "invalid_arguments"
    assert trace["tool_calls"][3]["error"]["code"] == "unknown_tool"
    assert all(c["ontology_version"] == services.store.version() and "duration_ms" in c for c in trace["tool_calls"])
    assert trace["metrics"] == {**trace["metrics"], "tool_call_count": 4, "tool_error_count": 2, "llm_turns": 5, "input_tokens": 50}
    assert "CL_NOT_CHARGING" in trace["subgraph"]["node_ids"]
    # The model saw the validation error, tied to its call, and only this configuration's tools.
    assert llm.requests[0]["tools"] == list(CONFIGS["B"].tools)
    tool_messages = [m for m in llm.requests[-1]["messages"] if m["role"] == "tool"]
    assert tool_messages[1]["tool_call_id"] == "c2" and "invalid_arguments" in tool_messages[1]["content"]


def test_tool_call_budget_and_llm_failures(services):
    endless = Scripted([call("get_schema", {}, f"c{i}") for i in range(3)] + [say("Out of budget; here is what I have.")])
    trace = AgentRuntime(services, endless).run("anything", "C", max_tool_calls=3)
    assert trace["stop_reason"] == "max_tool_calls" and trace["metrics"]["tool_call_count"] == 3
    assert endless.requests[-1]["tools"] == [] and "budget" in endless.requests[-1]["messages"][-1]["content"]
    failed = AgentRuntime(services, Scripted([LLMError("provider down")])).run("anything", "A")
    assert failed["stop_reason"] == "llm_error" and failed["error"] == "provider down" and failed["final_answer"] == ""


def test_disabling_tools_and_editing_descriptions_are_per_request(services):
    llm = Scripted([say("ok"), say("ok")])
    runtime = AgentRuntime(services, llm)
    runtime.run("q", "C", disabled_tools=["traverse_graph", "find_path"], description_overrides={"get_schema": "EDITED"})
    assert "traverse_graph" not in llm.requests[0]["tools"] and len(llm.requests[0]["tools"]) == 8
    runtime.run("q", "C")
    assert "traverse_graph" in llm.requests[1]["tools"]
    assert services.registry.llm_tools(["get_schema"], {"get_schema": "EDITED"})[0]["description"] == "EDITED"
    assert services.registry.llm_tools(["get_schema"])[0]["description"] != "EDITED"


def test_mock_runs_are_labelled(runtime):
    trace = runtime.run("What diagnostic steps are associated with laptop not charging?", "D")
    assert trace["is_mock"] is True and trace["llm"]["provider"] == "mock" and trace["final_answer"].startswith("[MOCK LLM]")
    assert trace["tool_calls"][0]["tool"] == "search_ontology_profiles"


def test_benchmark_is_well_formed(evaluation, services):
    tasks = evaluation.tasks
    assert len(tasks) >= 30
    assert {t.split for t in tasks} == {"dev", "test"} and sum(t.split == "test" for t in tasks) >= 20
    assert {t.category for t in tasks} == set(CATEGORIES)
    known = {n["node_id"] for n in services.store.all_nodes()}
    edges = {(e["source_node_id"], e["relationship_type"], e["target_node_id"]) for e in services.store.all_edges()}
    for task in tasks:
        for sequence in task.acceptable_sequences:
            assert all(services.registry.has(t) for t in sequence), task.id
        assert set(task.expected.answer_ids + task.evidence_ids()) <= known, task.id
        assert all(tuple(e) in edges for e in task.expected.evidence_edges), task.id
        for reference in task.expected.reference_queries:
            assert services.semantic.run(reference.query)["rows"], task.id


def test_coverage_is_about_reach_not_tool_count(evaluation):
    report = evaluation.coverage(split="all")["configs"]
    rate = {k: v["task_coverage_rate"] for k, v in report.items()}
    assert rate["A"] < rate["B"] < rate["C"] and rate["D"] > rate["B"] and rate["E"] > rate["D"]
    assert report["D"]["tool_count"] == 2 and report["B"]["tool_count"] == 10
    assert report["B"]["access"]["relationships"]["INDICATES"]["access"] == "none"
    assert report["C"]["access"]["relationships"]["INDICATES"]["access"] == "multi_call"
    assert report["D"]["access"]["relationships"]["INDICATES"] == {**report["D"]["access"]["relationships"]["INDICATES"], "access": "direct", "precomputed_only": True}
    assert report["D"]["access"]["operations"]["rule_evaluation"]["access"] == "none"
    assert report["E"]["access"]["operations"]["terminology_mapping"]["via"] == ["(prompt)"]
    unsupported = {u["task_id"] for u in report["C"]["unsupported_tasks"]}
    assert {"Q38", "Q39", "Q40"} <= unsupported


def test_disabling_a_tool_lowers_coverage_immediately(evaluation):
    full = evaluation.coverage(["C"], "all")["configs"]["C"]
    cut = evaluation.coverage(["C"], "all", disabled_tools=["get_neighbors", "traverse_graph"])["configs"]["C"]
    assert cut["task_coverage_rate"] < full["task_coverage_rate"] - 0.3
    assert cut["access"]["relationships"]["CONTAINS"]["via"] == ["find_path"]  # still readable, but only along a path
    none = evaluation.coverage(["C"], "all", disabled_tools=["get_neighbors", "traverse_graph", "find_path"])["configs"]["C"]
    assert none["access"]["relationships"]["CONTAINS"]["access"] == "none"


def test_number_matching_handles_percent_and_rounding():
    assert numbers_in("about 27.7% of 2,000 cases") == [(27.7, True), (2000.0, False)]
    assert number_matches(0.2765, "ratio", numbers_in("the rate is 27.7%"))
    assert number_matches(0.2765, "ratio", numbers_in("0.2765"))
    assert not number_matches(0.2765, "ratio", numbers_in("the rate is 31%"))
    assert number_matches(163, "integer", numbers_in("163 work orders")) and not number_matches(163, "integer", numbers_in("16.3"))


def fake_trace(config, answer, calls=(), stop="answered"):
    records = [{"index": i, "tool": name, "arguments": args, "ok": error is None, "result": result,
                "error": error, "duration_ms": 1.0, "ontology_version": "1.0.0", "provenance": {}, "generated_sql": None}
               for i, (name, args, result, error) in enumerate(calls, 1)]
    return {"config": config, "final_answer": answer, "stop_reason": stop, "error": None, "tool_calls": records,
            "metrics": {"tool_call_count": len(records), "input_tokens": 0, "output_tokens": 0, "latency_ms": 0}}


@pytest.fixture(scope="module")
def judge(services, evaluation):
    scorer, analyzer = Scorer(services), evaluation.analyzer()
    tasks = {t.id: t for t in evaluation.tasks}

    def score(task_id, config, answer, calls=(), stop="answered"):
        from ontolab.agents.configs import CONFIGS as configs
        coverage = analyzer.task_coverage(tasks[task_id], list(configs[config].tools), configs[config].prompt_operations)
        return scorer.score(tasks[task_id], fake_trace(config, answer, calls, stop), coverage)
    return score


CHECKLIST = {"checklists": [{"checklist_id": "CL_SWOLLEN_BATTERY", "name": "Swollen Battery Safety Checklist"}],
             "symptom": {"node_id": "S_BATTERY_SWOLLEN", "canonical_name": "Swollen Battery"}}


def test_correct_grounded_answer_scores_full_marks(judge):
    result = judge("Q04", "B", "That is Swollen Battery (S_BATTERY_SWOLLEN); use the Swollen Battery Safety Checklist.",
                   [("resolve_symptom", {"term": "bulging battery"}, {"resolved": {"node_id": "S_BATTERY_SWOLLEN"}}, None),
                    ("get_diagnostic_checklist", {"symptom_id": "S_BATTERY_SWOLLEN"}, CHECKLIST, None)])
    assert result["score"] == 2 and result["failure"] is None
    assert result["tool_use"]["tool_use_correct"] is True and result["evidence"]["retrieved"] is True
    assert result["grounding"]["hallucinated"] is False


def test_failures_are_attributed_to_the_right_layer(judge):
    wrong_tool = judge("Q04", "B", "I think it is a battery problem.", [("query_semantic_view", {"metrics": ["case_count"]}, {"rows": []}, None)])
    assert wrong_tool["failure"]["primary"] == "incorrect_tool_selection" and wrong_tool["failure"]["layer"] == "llm"

    bad_args = judge("Q04", "B", "Not sure.",
                     [("resolve_symptom", {"term": "bulging battery"}, {"resolved": {"node_id": "S_BATTERY_SWOLLEN"}}, None),
                      ("get_diagnostic_checklist", {"symptom_id": "S_BATTERY"}, {"checklists": [{"checklist_id": "CL_BATTERY"}]}, None)])
    assert bad_args["failure"]["primary"] == "incorrect_tool_arguments"

    misread = judge("Q04", "B", "Use the Battery Checklist.",
                    [("resolve_symptom", {"term": "bulging battery"}, {"resolved": {"node_id": "S_BATTERY_SWOLLEN"}}, None),
                     ("get_diagnostic_checklist", {"symptom_id": "S_BATTERY_SWOLLEN"}, CHECKLIST, None)])
    assert misread["failure"]["primary"] == "incorrect_result_interpretation" and misread["evidence"]["retrieved"] is True

    crashed = judge("Q04", "B", "Something went wrong.",
                    [("resolve_symptom", {"term": "bulging battery"}, None, {"code": "backend_error", "message": "boom", "details": {}})])
    assert crashed["failure"]["primary"] == "backend_execution_error" and crashed["failure"]["layer"] == "access_layer"

    no_coverage = judge("Q06", "B", "The Touchpad and the Operating System and Drivers can cause it.")
    assert no_coverage["covered"] is False and no_coverage["failure"]["primary"] == "insufficient_tool_coverage"
    assert "ungrounded_final_answer" in [c["category"] for c in no_coverage["failure"]["contributing"]]

    invented = judge("Q41", "B", "Step 1: restart. Step 2: reinstall the driver.", [("resolve_symptom", {"term": "touchpad"}, {}, None)])
    assert invented["failure"]["primary"] == "missing_ontology_knowledge" and invented["failure"]["layer"] == "ontology"
    assert all(r["failure"]["primary"] in FAILURE_CATEGORIES for r in (wrong_tool, bad_args, misread, crashed, no_coverage, invented))


def test_abstaining_clarifying_and_alternative_routes(judge):
    assert judge("Q06", "B", "I cannot answer this: none of my tools return the components a symptom indicates.")["success"] is True
    assert judge("Q41", "B", "No checklist is defined for Touchpad Not Working or its parent symptom.",
                 [("resolve_symptom", {"term": "touchpad"}, {}, None), ("get_diagnostic_checklist", {"symptom_id": "S_TOUCHPAD"}, {"checklists": []}, None)])["success"] is True
    asked = judge("Q34", "E", "That could be Black Screen or Display Failure. Does the laptop still power on, or is the panel damaged?")
    assert asked["score"] == 2
    guessed = judge("Q34", "E", "Follow the Black Screen Checklist.")
    assert guessed["score"] == 0
    # A route the benchmark did not list, but a correct and grounded answer: not a tool-use error.
    other = judge("Q04", "C", "It is Swollen Battery (S_BATTERY_SWOLLEN); use the Swollen Battery Safety Checklist (CL_SWOLLEN_BATTERY).",
                  [("query_objects", {"object_type": "Symptom"}, {"objects": [{"node_id": "S_BATTERY_SWOLLEN", "canonical_name": "Swollen Battery"}]}, None),
                   ("traverse_graph", {"start_node_id": "S_BATTERY_SWOLLEN"}, {"nodes": [{"node_id": "CL_SWOLLEN_BATTERY", "canonical_name": "Swollen Battery Safety Checklist"}]}, None)])
    assert other["success"] and other["tool_use"]["alternative_sequence"] is True and other["tool_use"]["tool_use_correct"] is True


def test_listing_everything_is_not_a_correct_answer(judge, services):
    everything = "; ".join(n["canonical_name"] for n in services.store.all_nodes("DiagnosticAction"))
    dumped = judge("Q29", "D", everything, [("search_ontology_profiles", {"query": "x"}, {"results": [{"profile": everything + " A_EXTERNAL_MONITOR_TEST"}]}, None)])
    assert dumped["answer"]["over_inclusive"] is True and dumped["score"] == 1


def test_hallucinated_objects_are_detected(judge):
    result = judge("Q04", "B", "Use the Swollen Battery Safety Checklist, then Reseat Memory Modules (A_RESEAT_RAM).",
                   [("get_diagnostic_checklist", {"symptom_id": "S_BATTERY_SWOLLEN"}, CHECKLIST, None)])
    assert result["grounding"]["hallucinated"] is True and "A_RESEAT_RAM" in result["grounding"]["ungrounded_objects"]


def test_end_to_end_mock_evaluation_is_recorded_and_flagged(evaluation):
    output = evaluation.run(configs=["A", "D"], split="dev", repeats=2, limit=4, label="pytest")
    assert output["is_mock"] is True and "MOCK" in output["notice"]
    assert len(output["results"]) == 2 * 4 * 2
    for key in ("A", "D"):
        summary = output["summary"]["by_config"][key]
        assert set(summary) >= {"coverage", "tool_calling", "end_to_end", "decomposition", "failure_attribution", "by_category", "by_complexity"}
        assert summary["end_to_end"]["run_to_run_consistency"] == 1.0  # the mock policy is deterministic
        assert 0 <= summary["end_to_end"]["success_rate"] <= 1
    stored = evaluation.load_run(output["run_id"])
    assert stored["settings"]["repeats"] == 2 and stored["results"][0]["trace"]["tool_calls"] is not None
    assert output["run_id"] in [r["run_id"] for r in evaluation.list_runs()]
    assert evaluation.load_run("../../etc/passwd") is None
