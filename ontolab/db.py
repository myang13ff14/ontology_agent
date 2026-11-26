"""DuckDB access. One connection per process, guarded by a lock."""

from __future__ import annotations

import json
import threading
from typing import Any, Iterable

import duckdb

DDL = """
CREATE TABLE IF NOT EXISTS ontology_meta (
    name VARCHAR, version VARCHAR, description VARCHAR, source VARCHAR,
    is_synthetic BOOLEAN, content_hash VARCHAR, profile_recipes VARCHAR, loaded_at TIMESTAMP
);
-- Ontology schema: definitions, kept apart from instance data.
CREATE TABLE IF NOT EXISTS ontology_object_type (
    name VARCHAR PRIMARY KEY, description VARCHAR, storage VARCHAR,
    table_name VARCHAR, key_column VARCHAR, name_column VARCHAR, ontology_version VARCHAR
);
CREATE TABLE IF NOT EXISTS ontology_relationship_type (
    name VARCHAR PRIMARY KEY, description VARCHAR, source_types VARCHAR, target_types VARCHAR,
    hierarchical BOOLEAN, storage VARCHAR, table_name VARCHAR, source_column VARCHAR,
    target_column VARCHAR, ontology_version VARCHAR
);
CREATE TABLE IF NOT EXISTS ontology_property_def (
    owner_kind VARCHAR, owner_name VARCHAR, name VARCHAR, data_type VARCHAR,
    required BOOLEAN, description VARCHAR
);
-- Ontology instances: the knowledge graph.
CREATE TABLE IF NOT EXISTS kg_node (
    node_id VARCHAR PRIMARY KEY, node_type VARCHAR, canonical_name VARCHAR, definition VARCHAR,
    synonyms VARCHAR, properties VARCHAR, source VARCHAR, ontology_version VARCHAR
);
CREATE TABLE IF NOT EXISTS kg_edge (
    edge_id VARCHAR PRIMARY KEY, source_node_id VARCHAR, target_node_id VARCHAR,
    relationship_type VARCHAR, properties VARCHAR, source VARCHAR, confidence DOUBLE,
    ontology_version VARCHAR
);
-- Experiment switch: relationship types or single edges hidden from every tool.
CREATE TABLE IF NOT EXISTS kg_disabled (kind VARCHAR, key VARCHAR);
CREATE OR REPLACE VIEW kg_edge_active AS
    SELECT * FROM kg_edge
    WHERE relationship_type NOT IN (SELECT key FROM kg_disabled WHERE kind = 'relationship_type')
      AND edge_id NOT IN (SELECT key FROM kg_disabled WHERE kind = 'edge');
-- Flattened GraphRAG profiles.
CREATE TABLE IF NOT EXISTS kg_profile (
    node_id VARCHAR PRIMARY KEY, node_type VARCHAR, title VARCHAR, text VARCHAR, structured VARCHAR,
    source_node_ids VARCHAR, source_edge_ids VARCHAR, embedding VARCHAR,
    ontology_version VARCHAR, profile_version VARCHAR, built_at TIMESTAMP
);
-- Operational data (synthetic).
CREATE TABLE IF NOT EXISTS service_case (
    case_id VARCHAR PRIMARY KEY, opened_at TIMESTAMP, closed_at TIMESTAMP, channel VARCHAR,
    device_model_id VARCHAR, symptom_id VARCHAR, call_driver_id VARCHAR, reported_text VARCHAR,
    handle_time_minutes DOUBLE, resolution_type VARCHAR, escalated BOOLEAN, in_warranty BOOLEAN,
    agent_id VARCHAR, is_synthetic BOOLEAN
);
CREATE TABLE IF NOT EXISTS diagnostic_execution (
    execution_id VARCHAR PRIMARY KEY, case_id VARCHAR, step_number INTEGER, action_id VARCHAR,
    outcome_id VARCHAR, succeeded BOOLEAN, duration_minutes DOUBLE, executed_at TIMESTAMP,
    is_synthetic BOOLEAN
);
CREATE TABLE IF NOT EXISTS work_order (
    work_order_id VARCHAR PRIMARY KEY, case_id VARCHAR, created_at TIMESTAMP, work_order_type VARCHAR,
    component_id VARCHAR, status VARCHAR, cost_usd DOUBLE, is_synthetic BOOLEAN
);
"""

ALL_TABLES = [
    "ontology_meta", "ontology_object_type", "ontology_relationship_type", "ontology_property_def",
    "kg_node", "kg_edge", "kg_disabled", "kg_profile", "service_case", "diagnostic_execution", "work_order",
]


class Database:
    def __init__(self, path: str = ":memory:"):
        self.path = path
        self._conn = duckdb.connect(path)
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute(DDL)

    def query(self, sql: str, params: Iterable[Any] | None = None) -> list[dict]:
        """Run a statement and return rows as dicts."""
        with self._lock:
            cursor = self._conn.execute(sql, list(params or []))
            if cursor.description is None:
                return []
            columns = [d[0] for d in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def execute(self, sql: str, params: Iterable[Any] | None = None) -> None:
        with self._lock:
            self._conn.execute(sql, list(params or []))

    def insert_many(self, table: str, rows: list[dict]) -> None:
        if not rows:
            return
        columns = list(rows[0])
        sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})"
        with self._lock:
            self._conn.executemany(sql, [[row[c] for c in columns] for row in rows])

    def scalar(self, sql: str, params: Iterable[Any] | None = None) -> Any:
        rows = self.query(sql, params)
        return next(iter(rows[0].values())) if rows else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def loads(value: str | None, default: Any = None) -> Any:
    if value in (None, ""):
        return default
    return json.loads(value)
