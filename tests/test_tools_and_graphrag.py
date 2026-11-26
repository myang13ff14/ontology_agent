import json
import time

from pydantic import BaseModel

from ontolab.tools.registry import Capability, ToolRegistry, ToolSpec


def run(registry, name, **arguments):
    invocation = registry.execute(name, arguments)
    assert invocation.ok, invocation.error
    return invocation.result


def test_every_tool_has_name_description_schemas_function_and_capabilities(registry):
    assert len(registry.names()) == 22
    for name in registry.names():
        info = registry.describe(name)
        assert len(info["description"]) > 40
        assert info["input_schema"]["type"] == "object" and "$defs" not in json.dumps(info["input_schema"])
        assert info["output_schema"]["type"] == "object"
        assert callable(registry.get(name).fn)
        assert set(info["capabilities"]) == {"entry_objects", "input_objects", "output_objects", "relationships", "operations", "access"}


def test_arguments_are_validated_before_execution(registry):
    bad_type = registry.execute("traverse_graph", {"start_node_id": "S_POWER", "max_depth": "deep"})
    assert not bad_type.ok and bad_type.error["code"] == "invalid_arguments"
    assert bad_type.error["details"]["problems"][0]["field"] == "max_depth"
    assert registry.execute("get_neighbors", {}).error["code"] == "invalid_arguments"
    assert registry.execute("get_neighbors", "S_POWER").error["code"] == "invalid_arguments"
    assert registry.execute("drop_everything", {}).error["code"] == "unknown_tool"
    assert registry.execute("get_schema", {}, allowed=["search_objects"]).error["code"] == "unknown_tool"


def test_invocations_record_timing_version_provenance_and_sql(registry, store):
    invocation = registry.execute("get_neighbors", {"object_id": "S_NOT_CHARGING", "relationship_type": "INDICATES"})
    assert invocation.ok and invocation.duration_ms >= 0 and invocation.ontology_version == store.version()
    assert "S_NOT_CHARGING" in invocation.provenance["node_ids"] and len(invocation.provenance["edge_ids"]) == 5
    sql = registry.execute("query_semantic_view", {"metrics": ["case_count"]})
    assert sql.generated_sql.startswith("SELECT")


def test_timeouts_and_backend_faults_become_structured_errors():
    class Args(BaseModel):
        seconds: float = 0

    def slow(args):
        time.sleep(args.seconds)
        return {"ok": True}

    def broken(args):
        raise RuntimeError("boom")

    registry = ToolRegistry(lambda: "test", timeout_s=0.2)
    registry.register(ToolSpec("slow", "Sleeps for a while, used to test time limits.", Args, {"type": "object"}, slow, Capability(), "generic"))
    registry.register(ToolSpec("broken", "Always fails, used to test error handling.", Args, {"type": "object"}, broken, Capability(), "generic"))
    assert registry.execute("slow", {"seconds": 1}).error["code"] == "timeout"
    assert registry.execute("slow", {}).ok
    failed = registry.execute("broken", {})
    assert failed.error["code"] == "backend_error" and "boom" in failed.error["message"]


def test_domain_tools_walk_the_troubleshooting_chain(registry):
    resolved = run(registry, "resolve_symptom", term="battery not charging")
    assert resolved["resolved"]["node_id"] == "S_NOT_CHARGING"
    assert run(registry, "resolve_symptom", term="screen")["ambiguous"] is True
    inherited = run(registry, "get_diagnostic_checklist", symptom_id="S_CHARGING_INTERMITTENT")
    assert inherited["checklists"][0]["checklist_id"] == "CL_NOT_CHARGING"
    assert inherited["inherited_from"]["node_id"] == "S_NOT_CHARGING"
    assert run(registry, "get_diagnostic_checklist", symptom_id="S_TOUCHPAD")["checklists"] == []
    steps = run(registry, "get_diagnostic_actions", checklist_id="CL_NO_POWER")["actions"]
    assert [a["step"] for a in steps] == [1, 2, 3, 4, 5, 6] and steps[2]["action_id"] == "A_POWER_RESET"
    assert {o["code"] for o in run(registry, "get_possible_outcomes", action_id="A_POWER_RESET")["outcomes"]} == {"FIXED", "NO_CHANGE"}
    assert registry.execute("get_diagnostic_actions", {"checklist_id": "S_POWER"}).error["code"] == "wrong_object_type"


def test_next_action_depends_on_context(registry):
    def nxt(**context):
        return run(registry, "get_next_action", action_id="A_POWER_RESET", outcome="no change", context=context)

    assert [a["action_id"] for a in nxt(checklist_id="CL_BLACK_SCREEN")["next_actions"]] == ["A_EXTERNAL_MONITOR_TEST"]
    assert [a["action_id"] for a in nxt(checklist_id="CL_NO_POWER")["next_actions"]] == ["A_RESEAT_BATTERY"]
    unknown = nxt()
    assert unknown["next_actions"] == [] and len(unknown["undetermined_branches"]) == 3
    assert unknown["undetermined_branches"][0]["missing_context"] == ["checklist_id"]
    fixed = run(registry, "get_next_action", action_id="A_POWER_RESET", outcome="FIXED", context={})
    assert fixed["next_actions"] == [] and "ends the procedure" in fixed["note"]
    assert registry.execute("get_next_action", {"action_id": "A_POWER_RESET", "outcome": "exploded"}).error["code"] == "unknown_outcome"


def test_graph_links_do_not_imply_eligibility(registry):
    listed = run(registry, "get_action_constraints", action_id="A_BIOS_UPDATE")
    assert {c["rule_id"] for c in listed["constraints"]} == {"R_BIOS_POWER", "R_ADMIN_RIGHTS"} and listed["eligible"] is None
    blocked = run(registry, "get_action_constraints", action_id="A_BIOS_UPDATE",
                  context={"ac_connected": True, "battery_percent": 10, "has_admin_rights": True})
    assert blocked["eligible"] is False
    allowed = run(registry, "get_action_constraints", action_id="A_BIOS_UPDATE",
                  context={"ac_connected": True, "battery_percent": 80, "has_admin_rights": True})
    assert allowed["eligible"] is True
    verdict = run(registry, "evaluate_rule", rule_id="R_OPEN_CHASSIS", context={"device_powered_off": True})
    assert verdict["satisfied"] is None and set(verdict["missing_context"]) == {"ac_connected", "customer_technical"}
    assert run(registry, "evaluate_rule", expression="checklist_id == 'CL_X'", context={"checklist_id": "CL_X"})["satisfied"] is True
    assert registry.execute("evaluate_rule", {"rule_id": "S_POWER"}).error["code"] == "not_a_rule"


def test_knowledge_articles_expose_status_and_supersession(registry):
    articles = run(registry, "get_related_knowledge", action_id="A_POWER_RESET")["articles"]
    assert [a["article_id"] for a in articles] == ["KA_1044", "KA_1012"]
    assert articles[0]["status"] == "current" and articles[1]["superseded_by"] == ["KA_1044"]


def test_generic_tools_have_no_domain_knowledge():
    import inspect

    from ontolab.ontology import store as store_module
    from ontolab.tools import generic
    source = inspect.getsource(generic) + inspect.getsource(store_module)
    for word in ("Symptom", "Checklist", "DiagnosticAction", "laptop", "battery", "HAS_CHECKLIST", "SUBTYPE_OF"):
        assert word not in source, f"generic layer mentions '{word}'"


def test_profiles_flatten_hierarchy_descendants_and_related_knowledge(services):
    profile = next(p for p in services.profiles.profiles if p["node_id"] == "S_NOT_CHARGING")
    text = profile["text"]
    assert "Also called: not charging" in text and "Parent concepts: Power Issue (S_POWER)" in text
    assert "Across all descendants - INDICATES" in text and "step_order=5" in text
    assert profile["structured"]["descendants"] == ["S_CHARGING_INTERMITTENT", "S_PLUGGED_NOT_CHARGING"]
    assert "CL_NOT_CHARGING" in profile["source_node_ids"] and profile["source_edge_ids"]
    child = next(p for p in services.profiles.profiles if p["node_id"] == "S_CHARGING_INTERMITTENT")
    assert "Inherited from Laptop Not Charging (S_NOT_CHARGING) - HAS_CHECKLIST" in child["text"]


def test_hybrid_search_and_staleness(services, store, restore_graph):
    index = services.profiles
    top = index.search("laptop not charging diagnostic steps", 3)
    assert top["results"][0]["node_id"] == "S_NOT_CHARGING" and top["stale"] is False
    assert top["results"][0]["keyword_score"] > 0 and top["results"][0]["vector_score"] > 0
    assert index.search("hard reset", 3, "DiagnosticAction")["results"][0]["node_id"] == "A_POWER_RESET"
    version = index.profile_version
    store.set_disabled(["INDICATES"])
    assert index.is_stale() and index.search("battery", 1)["stale"] is True
    rebuilt = index.rebuild()
    assert rebuilt["stale"] is False and rebuilt["profile_version"] != version
    assert "INDICATES" not in next(p for p in index.profiles if p["node_id"] == "S_NOT_CHARGING")["text"]
    store.set_disabled([])
    index.rebuild()
    assert index.profile_version == version


def test_terminology_mappings_are_external_versioned_and_typed(services):
    terminology = services.terminology
    assert terminology.version and terminology.path.name == "terminology_mappings.yaml"
    exact = terminology.resolve("my computer won't take a charge")["matches"][0]
    assert exact["kind"] == "authoritative" and [t["node_id"] for t in exact["targets"]] == ["S_NOT_CHARGING"]
    vague = terminology.resolve("the screen is dead")["matches"][0]
    assert vague["needs_clarification"] and {t["node_id"] for t in vague["targets"]} == {"S_BLACK_SCREEN", "S_DISPLAY_FAILURE"}
    group = terminology.resolve("power-related problems")["matches"][0]
    assert group["kind"] == "collection" and len(group["targets"]) == 5
    assert terminology.resolve("printer is jammed")["matches"] == []
    for mapping in terminology.catalog()["mappings"]:
        assert len(mapping["targets_resolved"]) == len(mapping["targets"]), mapping["term"]
