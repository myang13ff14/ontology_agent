"""Ingestion: a validated ontology definition -> relational schema, node and edge tables."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from ..db import Database, dumps
from .definition import OntologyDefinition, load_definition

ONTOLOGY_TABLES = [
    "ontology_meta", "ontology_object_type", "ontology_relationship_type", "ontology_property_def",
    "kg_node", "kg_edge", "kg_disabled",
]


def content_hash(definition: OntologyDefinition) -> str:
    payload = json.dumps(definition.model_dump(), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def ingest(db: Database, definition: OntologyDefinition | str | Path) -> dict:
    """Replace the stored ontology with this definition. Returns a load summary."""
    if not isinstance(definition, OntologyDefinition):
        definition = load_definition(definition)
    else:
        definition.validate_constraints()
    info = definition.ontology
    source = info.source or info.name

    for table in ONTOLOGY_TABLES:
        db.execute(f"DELETE FROM {table}")

    db.insert_many("ontology_meta", [{
        "name": info.name, "version": info.version, "description": info.description, "source": info.source,
        "is_synthetic": info.synthetic, "content_hash": content_hash(definition),
        "profile_recipes": dumps(definition.profile_recipes),
        "loaded_at": datetime.now(timezone.utc).replace(tzinfo=None),
    }])
    db.insert_many("ontology_object_type", [{
        "name": t.name, "description": t.description, "storage": t.storage, "table_name": t.table,
        "key_column": t.key_column, "name_column": t.name_column, "ontology_version": info.version,
    } for t in definition.object_types])
    db.insert_many("ontology_relationship_type", [{
        "name": r.name, "description": r.description, "source_types": dumps(r.source_types),
        "target_types": dumps(r.target_types), "hierarchical": r.hierarchical, "storage": r.storage,
        "table_name": r.table, "source_column": r.source_column, "target_column": r.target_column,
        "ontology_version": info.version,
    } for r in definition.relationship_types])
    property_rows = []
    for kind, owners in (("object", definition.object_types), ("relationship", definition.relationship_types)):
        for owner in owners:
            for p in owner.properties:
                property_rows.append({
                    "owner_kind": kind, "owner_name": owner.name, "name": p.name, "data_type": p.type,
                    "required": p.required, "description": p.description,
                })
    db.insert_many("ontology_property_def", property_rows)

    db.insert_many("kg_node", [{
        "node_id": n.id, "node_type": n.type, "canonical_name": n.name, "definition": n.definition,
        "synonyms": dumps(n.synonyms), "properties": dumps(n.properties), "source": n.source or source,
        "ontology_version": info.version,
    } for n in definition.nodes])
    db.insert_many("kg_edge", [{
        "edge_id": f"E{i:04d}", "source_node_id": e.source, "target_node_id": e.target,
        "relationship_type": e.type, "properties": dumps(e.properties), "source": e.provenance or source,
        "confidence": e.confidence, "ontology_version": info.version,
    } for i, e in enumerate(definition.edges, 1)])

    return {
        "name": info.name, "version": info.version, "object_types": len(definition.object_types),
        "relationship_types": len(definition.relationship_types), "nodes": len(definition.nodes),
        "edges": len(definition.edges),
    }
