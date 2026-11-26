import pytest

from ontolab.semantic.view import SemanticError


def test_operational_data_is_synthetic_and_consistent_with_the_ontology(services):
    db = services.db
    assert db.scalar("SELECT COUNT(*) FROM service_case") == 600
    assert db.scalar("SELECT COUNT(*) FROM service_case WHERE NOT is_synthetic") == 0
    assert db.scalar("SELECT COUNT(*) FROM service_case c LEFT JOIN kg_node n ON n.node_id = c.symptom_id WHERE n.node_id IS NULL") == 0
    assert db.scalar("SELECT COUNT(*) FROM diagnostic_execution x LEFT JOIN kg_edge e ON e.source_node_id = x.action_id "
                     "AND e.target_node_id = x.outcome_id AND e.relationship_type = 'HAS_OUTCOME' WHERE e.edge_id IS NULL") == 0
    # A work order exists exactly for cases that ended in one.
    assert db.scalar("SELECT COUNT(*) FROM service_case WHERE resolution_type = 'work_order'") == db.scalar("SELECT COUNT(DISTINCT case_id) FROM work_order")


def test_generation_is_reproducible(services):
    from ontolab.db import Database
    from ontolab.ontology.ingest import ingest
    from ontolab.ontology.store import OntologyStore
    from ontolab.sample.operational import generate
    totals = []
    for _ in range(2):
        db = Database()
        ingest(db, services.settings.ontology_path)
        generate(db, OntologyStore(db), 150, seed=7)
        totals.append(db.query("SELECT resolution_type, COUNT(*) AS n, SUM(handle_time_minutes) AS t FROM service_case GROUP BY 1 ORDER BY 1"))
    assert totals[0] == totals[1]


def test_metrics_match_hand_written_sql(services):
    result = services.semantic.run({"metrics": ["fix_on_phone_rate", "escalation_rate", "case_count", "work_order_generation_rate"]})
    row = result["rows"][0]
    db = services.db
    total = db.scalar("SELECT COUNT(*) FROM service_case")
    assert row["case_count"] == total
    assert row["fix_on_phone_rate"] == pytest.approx(db.scalar("SELECT COUNT(*) FROM service_case WHERE resolution_type = 'fixed_on_phone'") / total, abs=1e-4)
    assert row["escalation_rate"] == pytest.approx(db.scalar("SELECT COUNT(*) FROM service_case WHERE escalated") / total, abs=1e-4)
    assert row["work_order_generation_rate"] == pytest.approx(db.scalar("SELECT COUNT(DISTINCT case_id) FROM work_order") / total, abs=1e-4)
    assert result["sql"].startswith("SELECT") and result["metric_definitions"]["fix_on_phone_rate"]["denominator"]


def test_grouping_filtering_and_ordering(services):
    result = services.semantic.run({
        "metrics": ["diagnostic_action_success_rate", "action_execution_count"], "dimensions": ["action_name"],
        "filters": [{"dimension": "symptom_id", "operator": "in", "value": ["S_NOT_CHARGING", "S_CHARGING_INTERMITTENT"]}],
        "order_by": "action_execution_count", "limit": 4})
    counts = [r["action_execution_count"] for r in result["rows"]]
    assert len(counts) == 4 and counts == sorted(counts, reverse=True)
    assert "IN ('S_NOT_CHARGING', 'S_CHARGING_INTERMITTENT')" in result["sql"]
    names = services.semantic.run({"dimensions": ["call_driver_id", "call_driver_name"]})["rows"]
    assert {"call_driver_id": "CD_DISPLAY", "call_driver_name": "Display"} in names


@pytest.mark.parametrize("query, code", [
    ({"metrics": ["nope"]}, "unknown_metric"),
    ({"metrics": ["case_count", "work_order_count"]}, "mixed_tables"),
    ({"metrics": ["case_count"], "dimensions": ["action_name"]}, "unknown_dimension"),
    ({"metrics": ["case_count"], "filters": [{"dimension": "channel", "operator": "in", "value": "chat"}]}, "invalid_filter"),
    ({"metrics": ["case_count"], "order_by": "channel"}, "invalid_order_by"),
    ({}, "empty_query"),
])
def test_invalid_queries_are_rejected_with_codes(services, query, code):
    with pytest.raises(SemanticError) as info:
        services.semantic.run(query)
    assert info.value.code == code


def test_filter_values_cannot_inject_sql(services):
    before = services.db.scalar("SELECT COUNT(*) FROM service_case")
    result = services.semantic.run({"metrics": ["case_count"], "filters": [
        {"dimension": "channel", "operator": "=", "value": "chat'; DELETE FROM service_case; --"}]})
    assert result["rows"][0]["case_count"] == 0
    assert services.db.scalar("SELECT COUNT(*) FROM service_case") == before
    with pytest.raises(SemanticError):  # dimension names are checked against the catalogue, never interpolated blindly
        services.semantic.run({"metrics": ["case_count"], "dimensions": ["channel; DROP TABLE service_case"]})
