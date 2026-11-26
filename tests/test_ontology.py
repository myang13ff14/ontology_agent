import copy

import pytest
import yaml

from ontolab.config import DATA_DIR, Limits
from ontolab.db import Database
from ontolab.ontology import rules
from ontolab.ontology.definition import OntologyDefinition, OntologyValidationError, load_definition
from ontolab.ontology.ingest import ingest
from ontolab.ontology.store import OntologyAccessError, OntologyStore

SAMPLE = DATA_DIR / "ontology" / "laptop_support.yaml"


def raw():
    return yaml.safe_load(SAMPLE.read_text())


def test_sample_ontology_is_valid_and_matches_its_generator():
    from ontolab.sample.laptop_support import build_definition
    definition = load_definition(SAMPLE)
    assert definition.ontology.synthetic is True
    assert len(definition.nodes) > 150 and len(definition.edges) > 300
    assert build_definition() == raw(), "regenerate with: python -m ontolab.sample.laptop_support"


@pytest.mark.parametrize("mutate, expected", [
    (lambda d: d["edges"].append({"source": "S_POWER", "type": "INDICATES", "target": "NOPE"}), "dangling endpoint"),
    (lambda d: d["edges"].append({"source": "S_POWER", "type": "CONTAINS", "target": "C_BATTERY"}), "not allowed"),
    (lambda d: d["edges"].append({"source": "S_POWER", "type": "SUBTYPE_OF", "target": "S_NOT_CHARGING"}), "has a cycle"),
    (lambda d: d["nodes"].append({"id": "X1", "type": "Gadget", "name": "x"}), "unknown object type"),
    (lambda d: d["nodes"][0]["properties"].update(colour="red"), "not defined in the schema"),
    (lambda d: d["nodes"].append(copy.deepcopy(d["nodes"][0])), "duplicate node id"),
    (lambda d: d["edges"].append({"source": "S_POWER", "type": "HAS_SYMPTOM", "target": "S_POWER"}), "operational"),
])
def test_constraint_violations_are_rejected(mutate, expected):
    data = raw()
    mutate(data)
    with pytest.raises(OntologyValidationError, match=expected):
        OntologyDefinition.model_validate(data).validate_constraints()


def test_schema_is_kept_apart_from_instances(store):
    schema = store.get_schema()
    types = {t["name"]: t for t in schema["object_types"]}
    assert types["Symptom"]["storage"] == "graph" and types["Symptom"]["instance_count"] > 20
    assert types["ServiceCase"]["storage"] == "operational" and types["ServiceCase"]["table"] == "service_case"
    rels = {r["name"]: r for r in schema["relationship_types"]}
    assert rels["SUBTYPE_OF"]["hierarchical"] is True
    assert rels["HAS_SYMPTOM"]["storage"] == "operational"
    node = store.get_object("S_NOT_CHARGING")
    assert {"node_id", "node_type", "canonical_name", "definition", "synonyms", "properties", "source", "ontology_version"} <= set(node)


def test_search_resolves_names_synonyms_and_ids(store):
    assert store.search_objects("hard reset")["matches"][0]["node_id"] == "A_POWER_RESET"
    assert store.search_objects("my laptop won't charge", "Symptom")["matches"][0]["node_id"] == "S_NOT_CHARGING"
    assert store.search_objects("KA_1044")["matches"][0]["match_type"] == "exact"
    assert store.search_objects("zzzz qqqq")["matches"] == []


def test_traversal_returns_paths_and_provenance(store):
    result = store.traverse_graph("S_NOT_CHARGING", ["HAS_CHECKLIST", "CONTAINS"], 2)
    by_id = {n["node_id"]: n for n in result["nodes"]}
    assert by_id["CL_NOT_CHARGING"]["depth"] == 1 and by_id["A_POWER_RESET"]["depth"] == 2
    path = by_id["A_POWER_RESET"]["path"]
    assert path["node_ids"] == ["S_NOT_CHARGING", "CL_NOT_CHARGING", "A_POWER_RESET"]
    assert path["relationship_types"] == ["HAS_CHECKLIST", "CONTAINS"] and len(path["edge_ids"]) == 2
    assert set(path["edge_ids"]) <= set(result["provenance"]["edge_ids"])
    assert result == store.traverse_graph("S_NOT_CHARGING", ["HAS_CHECKLIST", "CONTAINS"], 2), "must be deterministic"


def test_hierarchy_both_directions(store):
    h = store.hierarchy("S_NOT_CHARGING")
    assert [a["node_id"] for a in h["ancestors"]] == ["S_POWER"]
    assert {d["node_id"] for d in h["descendants"]} == {"S_CHARGING_INTERMITTENT", "S_PLUGGED_NOT_CHARGING"}


def test_depth_and_result_limits_are_enforced(store):
    deep = store.traverse_graph("S_POWER", None, 99, "both")
    assert deep["max_depth"] == store.limits.max_depth
    small = store.traverse_graph("A_POWER_RESET", None, 3, "both", limit=5)
    assert len(small["nodes"]) == 5 and small["truncated"] is True and small["total_reached"] > 5


def test_cycles_do_not_loop_forever():
    data = raw()
    data["relationship_types"].append({"name": "RELATED_TO", "source_types": ["Symptom"], "target_types": ["Symptom"]})
    data["edges"] += [{"source": a, "type": "RELATED_TO", "target": b}
                      for a, b in [("S_WIFI_SLOW", "S_WIFI_DROPS"), ("S_WIFI_DROPS", "S_WIFI_NOT_DETECTED"), ("S_WIFI_NOT_DETECTED", "S_WIFI_SLOW")]]
    db = Database()
    ingest(db, OntologyDefinition.model_validate(data))
    store = OntologyStore(db, Limits(max_depth=8))
    result = store.traverse_graph("S_WIFI_SLOW", ["RELATED_TO"], 8)
    assert {n["node_id"]: n["depth"] for n in result["nodes"]} == {"S_WIFI_DROPS": 1, "S_WIFI_NOT_DETECTED": 2}


def test_find_path_crosses_edge_directions(store):
    found = store.find_path("S_BLACK_SCREEN", "KA_1044")
    assert found["found"] and found["length"] == 3
    assert [s["relationship_type"] for s in found["steps"]] == ["HAS_CHECKLIST", "CONTAINS", "DESCRIBES"]
    assert found["steps"][-1]["direction"] == "in"
    assert store.find_path("S_BLACK_SCREEN", "KA_1044", max_depth=1)["found"] is False


def test_query_objects_on_graph_and_operational_types(store):
    removable = store.query_objects("DeviceModel", {"removable_battery": True})
    assert {o["node_id"] for o in removable["objects"]} == {"DM_WORK15", "DM_WORK14"}
    cases = store.query_objects("ServiceCase", {"symptom_id": "S_TOUCHPAD"}, limit=3)
    assert cases["storage"] == "operational" and len(cases["objects"]) == 3 and cases["total_matches"] > 3


@pytest.mark.parametrize("call, code", [
    (lambda s: s.get_object("NOPE"), "object_not_found"),
    (lambda s: s.traverse_graph("S_POWER", ["NOPE"]), "unknown_relationship_type"),
    (lambda s: s.get_neighbors("S_POWER", None, "sideways"), "invalid_argument"),
    (lambda s: s.search_objects("x", "Gadget"), "unknown_object_type"),
    (lambda s: s.query_objects("Symptom", {"colour": "red"}), "unknown_field"),
    (lambda s: s.query_objects("ServiceCase", {"1=1; DROP TABLE kg_node": 1}), "unknown_field"),
])
def test_errors_are_structured(store, call, code):
    with pytest.raises(OntologyAccessError) as info:
        call(store)
    assert info.value.code == code and info.value.message


def test_hiding_a_relationship_changes_the_version_not_the_data(store, restore_graph):
    before = store.version()
    store.set_disabled(["INDICATES"])
    assert store.version().startswith(before + "+exp.")
    assert store.get_neighbors("S_NOT_CHARGING", None, "out")["provenance"]["relationship_types"] == ["HAS_CALL_DRIVER", "HAS_CHECKLIST", "SUBTYPE_OF"]
    assert "INDICATES" not in {r["name"] for r in store.get_schema()["relationship_types"]}
    store.set_disabled([])
    assert store.version() == before
    assert "INDICATES" in store.get_neighbors("S_NOT_CHARGING", None, "out")["provenance"]["relationship_types"]


def test_rules_are_three_valued_and_safe():
    assert rules.evaluate("a == true and b >= 20", {"a": True, "b": 25})["satisfied"] is True
    assert rules.evaluate("a == true and b >= 20", {"a": True, "b": 5})["satisfied"] is False
    undecided = rules.evaluate("a == true and b >= 20", {"a": True})
    assert undecided["satisfied"] is None and undecided["missing_context"] == ["b"]
    assert rules.evaluate("x in ['p', 'q'] or not y", {"x": "z", "y": False})["satisfied"] is True
    # An unknown fact only matters when the outcome depends on it.
    assert rules.evaluate("a == 1 or b == 2", {"a": 1})["satisfied"] is True
    assert rules.evaluate("a == 1 or b == 2", {"a": 9})["satisfied"] is None
    assert rules.evaluate("a == 1 and b == 2", {"a": 9}) == {"satisfied": False, "missing_context": [], "expression": "a == 1 and b == 2"}
    assert rules.evaluate("not (a == 1 and b == 2)", {"b": 2})["satisfied"] is None
    for bad in ["__import__('os').system('ls')", "a.b == 1", "a if b else c", "lambda: 1", ""]:
        with pytest.raises(rules.RuleError):
            rules.evaluate(bad, {"a": 1, "b": 1, "c": 1})
