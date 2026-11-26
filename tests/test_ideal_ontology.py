"""The diagram-based troubleshooting ontology: valid, internally consistent, and loadable by the generic engine."""

from ontolab.config import DATA_DIR
from ontolab.db import Database
from ontolab.ontology import rules
from ontolab.ontology.definition import load_definition
from ontolab.ontology.ingest import ingest
from ontolab.ontology.store import OntologyStore

PATH = DATA_DIR / "ontology" / "troubleshooting_ideal.yaml"
DIAGRAM_RELATIONSHIPS = {"INVOLVES_DEVICE", "HAS_ASSESSMENT", "HAS_CHECK_RESULT", "SELECTED_SOLUTION", "SUPERSEDES",
                         "ASSESSES", "BASED_ON", "HAS_CHECKLIST", "CONTAINS_CHECK", "RESULT_OF"}


def load():
    definition = load_definition(PATH)
    nodes = {n.id: n for n in definition.nodes}
    out = {}
    for edge in definition.edges:
        out.setdefault((edge.source, edge.type), []).append(edge)
    return definition, nodes, out


def test_schema_matches_the_diagram():
    definition, _, _ = load()
    assert {t.name for t in definition.object_types} == {
        "SupportCase", "Device", "SymptomAssessment", "Symptom", "Checklist", "CheckDefinition", "CheckResult", "Solution"}
    assert {r.name for r in definition.relationship_types} == DIAGRAM_RELATIONSHIPS | {"RESOLVED_BY"}  # one flagged addition
    symptom = next(t for t in definition.object_types if t.name == "Symptom")
    assert {"typical_expressions", "distinguishing_conditions"} <= {p.name for p in symptom.properties if p.required}


def test_knowledge_is_internally_consistent():
    definition, nodes, out = load()
    codes = {n.id: {r["code"] for r in n.properties["possible_results"]} for n in definition.nodes if n.type == "CheckDefinition"}
    for symptom in (n for n in definition.nodes if n.type == "Symptom"):
        checklists = out[(symptom.id, "HAS_CHECKLIST")]
        working = [c for c in checklists if c.properties["role"] != "entitlement"]
        assert 2 <= len(working) <= 3, f"{symptom.id} should have two or three working checklists (plus entitlement where repairs apply)"
        assert sorted(c.properties["order"] for c in checklists) == list(range(1, len(checklists) + 1))
        assert checklists[0].properties["role"] == "triage" or any(c.properties["role"] == "triage" for c in checklists)
        on_checklist = {c.target for cl in checklists for c in out[(cl.target, "CONTAINS_CHECK")]}
        triage = {c.target for cl in checklists if cl.properties["role"] == "triage" for c in out[(cl.target, "CONTAINS_CHECK")]}
        assert {c["check"] for c in symptom.properties["distinguishing_conditions"]} <= triage, f"{symptom.id}: triage must confirm the symptom"
        for condition in symptom.properties["distinguishing_conditions"]:
            assert condition["expect"] in codes[condition["check"]], symptom.id
            assert condition["check"] in on_checklist, f"{symptom.id}: {condition['check']} is not on its checklist"
        assert out.get((symptom.id, "RESOLVED_BY")), f"{symptom.id} has no solution"
    for edge in definition.edges:
        if edge.type == "RESOLVED_BY" and edge.properties.get("condition"):
            assert set(rules.referenced_names(edge.properties["condition"])) <= set(codes)
    for result in (n for n in definition.nodes if n.type == "CheckResult"):
        assert result.properties["result"] in codes[out[(result.id, "RESULT_OF")][0].target], result.id


def test_scope_covers_hardware_software_and_warranty():
    definition, nodes, out = load()
    by_category = {}
    for symptom in (n for n in definition.nodes if n.type == "Symptom"):
        by_category.setdefault(symptom.properties["category"], []).append(symptom.id)
    assert set(by_category) == {"hardware", "software", "warranty"} and len(by_category["hardware"]) == 5 and len(by_category["software"]) == 2
    assert {n.properties["product_line"] for n in definition.nodes if n.type == "Device"} == {"ThinkPad", "IdeaPad", "Legion", "Yoga"}
    # Repairs and shipped parts are never offered without recorded cover; a paid quote needs it recorded as missing.
    paid = [e for e in definition.edges if e.type == "RESOLVED_BY" and e.target == "SOL_PAID_REPAIR_QUOTE"]
    assert len(paid) == 6
    for edge in definition.edges:
        if edge.type != "RESOLVED_BY":
            continue
        kind = nodes[edge.target].properties["solution_type"]
        if kind in ("depot_repair", "part_shipment"):
            names = rules.referenced_names(edge.properties["condition"])
            assert "CK_WARRANTY_STATUS" in names or "CK_ADP_COVER" in names, f"{edge.source} -> {edge.target} ignores entitlement"
    assert "illustrative" in definition.ontology.description and definition.ontology.synthetic


def test_every_check_and_solution_is_reachable():
    definition, nodes, out = load()
    used_checks = {e.target for e in definition.edges if e.type == "CONTAINS_CHECK"}
    used_solutions = {e.target for e in definition.edges if e.type == "RESOLVED_BY"}
    by_type = {t: [n.id for n in definition.nodes if n.type == t] for t in ("CheckDefinition", "Checklist", "Solution")}
    assert len(by_type["CheckDefinition"]) == 39 and len(by_type["Checklist"]) == 23 and len(by_type["Solution"]) == 28
    assert set(by_type["CheckDefinition"]) == used_checks, "every check definition is on a checklist"
    assert set(by_type["Solution"]) == used_solutions, "every solution resolves some symptom"
    assert set(by_type["Checklist"]) == {e.target for e in definition.edges if e.type == "HAS_CHECKLIST"}
    # A solution's condition may only use checks that are on that symptom's checklists.
    for symptom in (n for n in definition.nodes if n.type == "Symptom"):
        on_checklist = {c.target for cl in out[(symptom.id, "HAS_CHECKLIST")] for c in out[(cl.target, "CONTAINS_CHECK")]}
        for link in out[(symptom.id, "RESOLVED_BY")]:
            names = set(rules.referenced_names(link.properties.get("condition") or "true"))
            assert names <= on_checklist, f"{symptom.id} -> {link.target} needs {names - on_checklist}, not on its checklists"


def test_every_example_case_has_a_complete_evidence_chain():
    definition, nodes, out = load()
    cases = [n for n in definition.nodes if n.type == "SupportCase"]
    assert len(cases) == 5
    for case in cases:
        results = {out[(e.target, "RESULT_OF")][0].target: nodes[e.target].properties["result"] for e in out[(case.id, "HAS_CHECK_RESULT")]}
        assessments = sorted((nodes[e.target] for e in out[(case.id, "HAS_ASSESSMENT")]), key=lambda a: a.properties["sequence"])
        superseded = {e.target for a in assessments for e in out.get((a.id, "SUPERSEDES"), [])}
        # Append-only history: each later assessment replaces exactly the previous one and cites check results.
        for previous, current in zip(assessments, assessments[1:]):
            assert [e.target for e in out[(current.id, "SUPERSEDES")]] == [previous.id]
            assert out.get((current.id, "BASED_ON")), current.id
            assert current.properties["assessed_at"] > previous.properties["assessed_at"]
        assert all((a.properties["status"] == "superseded") == (a.id in superseded) for a in assessments)
        final = assessments[-1]
        confirmed = [e.target for e in out[(final.id, "ASSESSES")] if e.properties["verdict"] == "confirmed"]
        assert final.properties["status"] == "confirmed" and len(confirmed) == 1, case.id
        symptom = nodes[confirmed[0]]
        assert all(results.get(c["check"]) == c["expect"] for c in symptom.properties["distinguishing_conditions"]), case.id
        chosen = out[(case.id, "SELECTED_SOLUTION")][0].target
        applicable = [e.target for e in out[(symptom.id, "RESOLVED_BY")]
                      if not e.properties.get("condition") or rules.evaluate(e.properties["condition"], results)["satisfied"] is True]
        assert chosen in applicable, f"{case.id}: {chosen} not among {applicable}"


def test_the_generic_engine_can_load_and_traverse_it():
    db = Database()
    summary = ingest(db, PATH)
    assert summary["object_types"] == 8 and summary["nodes"] == 138
    store = OntologyStore(db)
    history = store.traverse_graph("AS_001_3", ["SUPERSEDES"], 3)
    assert [n["node_id"] for n in history["nodes"]] == ["AS_001_2", "AS_001_1"]
    path = store.find_path("CASE_003", "CK_BATTERY_HEALTH")
    assert [s["relationship_type"] for s in path["steps"]] == ["HAS_CHECK_RESULT", "RESULT_OF"]
