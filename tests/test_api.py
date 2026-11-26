import time

import pytest
from fastapi.testclient import TestClient

from ontolab.api import main
from ontolab.llm.mock import MockLLM


@pytest.fixture(scope="module")
def client(services):
    main.install(services, MockLLM())
    with TestClient(main.app) as test_client:
        yield test_client
    services.store.set_disabled([])


def test_status_and_frontend(client):
    status = client.get("/status").json()
    assert status["llm"]["is_mock"] is True and status["ontology"]["is_synthetic"] is True
    assert "synthetic" in status["data_notice"].lower()
    page = client.get("/")
    assert page.status_code == 200 and "Ontology Agent Lab" in page.text
    assert client.get("/js/app.js").status_code == 200


def test_ontology_endpoints(client):
    assert len(client.get("/ontology/schema").json()["object_types"]) == 13
    node = client.get("/ontology/node/S_NOT_CHARGING").json()
    assert node["node"]["canonical_name"] == "Laptop Not Charging" and len(node["neighbors"]) >= 8
    assert client.get("/ontology/node/NOPE").status_code == 404
    graph = client.get("/ontology/graph", params={"focus": "S_NOT_CHARGING", "depth": 1}).json()
    assert len(graph["nodes"]) == 11 and all({"edge_id", "confidence", "source"} <= set(e) for e in graph["edges"])
    typed = client.get("/ontology/graph", params={"object_types": "Symptom", "relationship_types": "SUBTYPE_OF"}).json()
    assert {e["relationship_type"] for e in typed["edges"]} == {"SUBTYPE_OF"}


def test_tools_can_be_listed_and_run_manually(client):
    tools = client.get("/tools").json()["tools"]
    assert len(tools) == 22 and next(t for t in tools if t["name"] == "resolve_symptom")["configs"] == ["B"]
    ok = client.post("/tools/find_path/execute", json={"arguments": {"source_node_id": "S_BLACK_SCREEN", "target_node_id": "KA_1044"}}).json()
    assert ok["ok"] and ok["result"]["length"] == 3
    bad = client.post("/tools/find_path/execute", json={"arguments": {}}).json()
    assert bad["ok"] is False and bad["error"]["code"] == "invalid_arguments"
    assert client.post("/tools/nope/execute", json={"arguments": {}}).status_code == 404


def test_agent_run_and_validation(client):
    trace = client.post("/agents/run", json={"question": "What is the escalation rate?", "config": "A"}).json()
    assert trace["is_mock"] and trace["generated_sql"] and trace["tools_available"] == ["query_semantic_view"]
    limited = client.post("/agents/run", json={"question": "steps for not charging", "config": "C", "disabled_tools": ["search_objects"], "max_tool_calls": 1}).json()
    assert "search_objects" not in limited["tools_available"] and limited["metrics"]["tool_call_count"] <= 1
    assert client.post("/agents/run", json={"question": "x", "config": "Z"}).status_code == 400
    assert client.post("/agents/run", json={"question": "  ", "config": "A"}).status_code == 400
    assert client.post("/agents/run", json={"question": "x", "config": "A", "llm_provider": "openai"}).status_code == 400  # no model configured


def test_ontology_experiment_switch_marks_profiles_stale(client):
    changed = client.post("/ontology/experiment/disable", json={"relationship_types": ["DESCRIBES"]}).json()
    assert changed["disabled"]["relationship_types"] == ["DESCRIBES"] and "+exp." in changed["ontology_version"]
    assert changed["profiles"]["stale"] is True
    assert client.post("/tools/get_related_knowledge/execute", json={"arguments": {"action_id": "A_POWER_RESET"}}).json()["result"]["articles"] == []
    assert client.post("/ontology/experiment/disable", json={"relationship_types": ["NOPE"]}).status_code == 400
    restored = client.post("/ontology/experiment/disable", json={"relationship_types": []}).json()
    assert restored["profiles"]["stale"] is False


def test_coverage_and_background_evaluation(client):
    coverage = client.post("/coverage", json={"split": "test", "disabled_tools": ["search_ontology_profiles"]}).json()
    assert coverage["configs"]["D"]["task_coverage_rate"] < 0.3
    job = client.post("/evaluation/run", json={"configs": ["A"], "split": "dev", "limit": 3, "label": "api test"}).json()
    for _ in range(100):
        state = client.get(f"/evaluation/jobs/{job['job_id']}").json()
        if state["status"] != "running":
            break
        time.sleep(0.1)
    assert state["status"] == "finished" and state["done"] == 3
    run = client.get(f"/evaluation/runs/{state['run_id']}").json()
    assert run["is_mock"] and "trace" not in run["results"][0]
    first = run["results"][0]
    detail = client.get(f"/evaluation/runs/{state['run_id']}/trace", params={"task_id": first["task_id"], "config": "A", "repeat": 1}).json()
    assert detail["trace"]["question"] == first["question"]
    assert client.get("/evaluation/runs/nope").status_code == 404


def test_conversations_remember_earlier_turns(client):
    first = client.post("/agents/run", json={"question": "What is the escalation rate?", "config": "A"}).json()
    conversation = first["conversation"]
    assert conversation["turn"] == 1 and conversation["remembered_turns"] == 1 and first["memory"]["replayed_messages"] == 0
    assert "_turn_messages" not in first
    second = client.post("/agents/run", json={"question": "And the fix on phone rate?", "config": "A",
                                              "conversation_id": conversation["conversation_id"]}).json()
    assert second["conversation"]["turn"] == 2 and second["memory"]["replayed_messages"] == conversation["remembered_messages"]
    assert second["conversation"]["remembered_questions"] == ["What is the escalation rate?", "And the fix on phone rate?"]
    assert second["metrics"]["tool_call_count"] == len(second["tool_calls"])

    info = client.get(f"/agents/conversations/{conversation['conversation_id']}").json()
    assert info["turn"] == 2
    # A conversation belongs to one configuration, and can be forgotten.
    wrong = client.post("/agents/run", json={"question": "x", "config": "B", "conversation_id": conversation["conversation_id"]})
    assert wrong.status_code == 400
    assert client.delete(f"/agents/conversations/{conversation['conversation_id']}").json() == {"forgotten": True}
    gone = client.post("/agents/run", json={"question": "x", "config": "A", "conversation_id": conversation["conversation_id"]})
    assert gone.status_code == 404
    # Memory can be switched off for a single request.
    stateless = client.post("/agents/run", json={"question": "What is the escalation rate?", "config": "A", "remember": False}).json()
    assert stateless["conversation"] is None
    assert client.get("/status").json()["memory"]["window_turns"] == 6


def test_raw_data_can_be_browsed_but_only_known_tables(client):
    tables = {t["name"]: t for t in client.get("/data/tables").json()["tables"]}
    assert {"kg_node", "kg_edge", "service_case", "ontology_object_type", "sv_cases"} <= set(tables)
    assert tables["kg_node"]["rows"] == 188 and "node_id" in [c["name"] for c in tables["kg_node"]["columns"]]
    page = client.get("/data/table/kg_node", params={"limit": 5, "offset": 5}).json()
    assert page["total"] == 188 and len(page["rows"]) == 5 and page["offset"] == 5
    found = client.get("/data/table/kg_node", params={"search": "hard reset"}).json()
    assert found["total"] == 1 and found["rows"][0]["node_id"] == "A_POWER_RESET"
    exact = client.get("/data/table/kg_edge", params={"column": "relationship_type", "value": "SUPERSEDES"}).json()
    assert exact["total"] == 1
    assert "embedding" not in client.get("/data/table/kg_profile", params={"limit": 1}).json()["rows"][0]
    assert client.get("/data/table/kg_node; DROP TABLE kg_node").status_code == 404
    assert client.get("/data/table/information_schema.tables").status_code == 404
    assert client.get("/data/table/kg_edge", params={"column": "nope", "value": "x"}).status_code == 400
    other = client.get("/data/tables", params={"workspace": "troubleshooting"}).json()
    assert "service_case" not in [t["name"] for t in other["tables"]]
    assert client.get("/data/table/kg_node", params={"workspace": "troubleshooting", "search": "CASE_001"}).json()["total"] >= 1
