"""Generic, domain-independent access to the ontology stored in relational tables.

Nothing here knows about any particular domain: every operation works from the
ontology schema tables and the KG_NODE / KG_EDGE tables. Traversal is done in SQL
with recursive CTEs and is bounded (depth and result limits), cycle-safe and
deterministic. Every graph result carries provenance: node ids, edge ids,
relationship types and paths.
"""

from __future__ import annotations

import hashlib
from typing import Any

from ..config import Limits
import uuid

from ..db import Database, dumps, loads
from ..errors import ToolError
from ..text import contains_phrase, normalize, overlap

DIRECTIONS = ("out", "in", "both")


class OntologyAccessError(ToolError):
    """Raised for bad arguments or missing objects in ontology access."""


def _node(row: dict, brief: bool = False) -> dict:
    out = {"node_id": row["node_id"], "node_type": row["node_type"], "canonical_name": row["canonical_name"]}
    if not brief:
        out.update({
            "definition": row["definition"], "synonyms": loads(row["synonyms"], []),
            "properties": loads(row["properties"], {}), "source": row["source"],
            "ontology_version": row["ontology_version"],
        })
    return out


class OntologyStore:
    def __init__(self, db: Database, limits: Limits | None = None):
        self.db = db
        self.limits = limits or Limits()

    # ----------------------------------------------------------- versioning

    def meta(self) -> dict:
        rows = self.db.query("SELECT * FROM ontology_meta")
        if not rows:
            raise OntologyAccessError("ontology_not_loaded", "No ontology has been ingested.")
        return rows[0]

    def disabled(self) -> dict:
        rows = self.db.query("SELECT kind, key FROM kg_disabled ORDER BY kind, key")
        return {
            "relationship_types": [r["key"] for r in rows if r["kind"] == "relationship_type"],
            "edges": [r["key"] for r in rows if r["kind"] == "edge"],
        }

    def version(self) -> str:
        """Ontology version, tagged when an experiment has hidden part of the graph."""
        base = self.meta()["version"]
        disabled = self.disabled()
        if not (disabled["relationship_types"] or disabled["edges"]):
            return base
        tag = hashlib.sha256(repr(disabled).encode()).hexdigest()[:6]
        return f"{base}+exp.{tag}"

    def graph_hash(self) -> str:
        """Hash of the graph as tools currently see it. Used to detect stale profiles."""
        nodes = self.db.query("SELECT node_id, canonical_name, definition, synonyms, properties FROM kg_node ORDER BY node_id")
        edges = self.db.query(
            "SELECT edge_id, source_node_id, target_node_id, relationship_type, properties FROM kg_edge_active ORDER BY edge_id")
        return hashlib.sha256(repr((nodes, edges)).encode()).hexdigest()[:12]

    def set_disabled(self, relationship_types: list[str] | None = None, edges: list[str] | None = None) -> dict:
        """Experiment control: hide relationship types / edges from all tools (no data is deleted)."""
        known = {r["name"] for r in self.db.query("SELECT name FROM ontology_relationship_type")}
        unknown = [r for r in (relationship_types or []) if r not in known]
        if unknown:
            raise OntologyAccessError("unknown_relationship_type", f"Unknown relationship types: {unknown}",
                                      valid=sorted(known))
        self.db.execute("DELETE FROM kg_disabled")
        self.db.insert_many("kg_disabled", [{"kind": "relationship_type", "key": r} for r in relationship_types or []]
                            + [{"kind": "edge", "key": e} for e in edges or []])
        return self.disabled()

    # ---------------------------------------------------------------- schema

    def object_types(self) -> dict[str, dict]:
        props = self.db.query("SELECT * FROM ontology_property_def WHERE owner_kind = 'object' ORDER BY owner_name, name")
        counts = {r["node_type"]: r["n"] for r in
                  self.db.query("SELECT node_type, COUNT(*) AS n FROM kg_node GROUP BY node_type")}
        out = {}
        for row in self.db.query("SELECT * FROM ontology_object_type ORDER BY name"):
            out[row["name"]] = {
                "name": row["name"], "description": row["description"], "storage": row["storage"],
                "table": row["table_name"], "key_column": row["key_column"], "name_column": row["name_column"],
                "properties": [{"name": p["name"], "type": p["data_type"], "required": p["required"],
                                "description": p["description"]} for p in props if p["owner_name"] == row["name"]],
                "instance_count": counts.get(row["name"], 0) if row["storage"] == "graph" else None,
            }
        return out

    def relationship_types(self) -> dict[str, dict]:
        props = self.db.query(
            "SELECT * FROM ontology_property_def WHERE owner_kind = 'relationship' ORDER BY owner_name, name")
        counts = {r["relationship_type"]: r["n"] for r in self.db.query(
            "SELECT relationship_type, COUNT(*) AS n FROM kg_edge_active GROUP BY relationship_type")}
        disabled = set(self.disabled()["relationship_types"])
        out = {}
        for row in self.db.query("SELECT * FROM ontology_relationship_type ORDER BY name"):
            out[row["name"]] = {
                "name": row["name"], "description": row["description"],
                "source_types": loads(row["source_types"], []), "target_types": loads(row["target_types"], []),
                "hierarchical": row["hierarchical"], "storage": row["storage"], "table": row["table_name"],
                "source_column": row["source_column"], "target_column": row["target_column"],
                "properties": [{"name": p["name"], "type": p["data_type"], "required": p["required"],
                                "description": p["description"]} for p in props if p["owner_name"] == row["name"]],
                "edge_count": counts.get(row["name"], 0) if row["storage"] == "graph" else None,
                "disabled": row["name"] in disabled,
            }
        return out

    def get_schema(self) -> dict:
        meta = self.meta()
        return {
            "ontology": {"name": meta["name"], "version": self.version(), "description": meta["description"],
                         "source": meta["source"], "is_synthetic": meta["is_synthetic"]},
            "object_types": list(self.object_types().values()),
            "relationship_types": [r for r in self.relationship_types().values() if not r["disabled"]],
        }

    def hierarchical_relationships(self) -> list[str]:
        return [name for name, r in self.relationship_types().items()
                if r["hierarchical"] and r["storage"] == "graph" and not r["disabled"]]

    def profile_recipes(self) -> dict[str, list[list[str]]]:
        return loads(self.meta()["profile_recipes"], {})

    # --------------------------------------------------------------- objects

    def _limit(self, limit: int | None, default: int | None = None) -> int:
        value = limit if limit is not None else (default or self.limits.default_results)
        if not isinstance(value, int) or value < 1:
            raise OntologyAccessError("invalid_argument", "limit must be a positive integer.", limit=limit)
        return min(value, self.limits.max_results)

    def _require_type(self, object_type: str) -> dict:
        types = self.object_types()
        if object_type not in types:
            raise OntologyAccessError("unknown_object_type", f"Unknown object type '{object_type}'.",
                                      valid=sorted(types))
        return types[object_type]

    def _require_relationships(self, relationship_types: list[str] | None) -> list[str] | None:
        if not relationship_types:
            return None
        known = self.relationship_types()
        unknown = [r for r in relationship_types if r not in known]
        if unknown:
            raise OntologyAccessError("unknown_relationship_type", f"Unknown relationship types: {unknown}",
                                      valid=sorted(known))
        return sorted(set(relationship_types))

    def get_object(self, object_id: str) -> dict:
        rows = self.db.query("SELECT * FROM kg_node WHERE node_id = ?", [object_id])
        if not rows:
            raise OntologyAccessError("object_not_found", f"No object with id '{object_id}'.", object_id=object_id)
        return _node(rows[0])

    def get_objects(self, object_ids: list[str], brief: bool = True) -> dict[str, dict]:
        if not object_ids:
            return {}
        marks = ", ".join("?" for _ in object_ids)
        rows = self.db.query(f"SELECT * FROM kg_node WHERE node_id IN ({marks})", object_ids)
        return {r["node_id"]: _node(r, brief) for r in rows}

    def all_nodes(self, object_type: str | None = None) -> list[dict]:
        if object_type:
            rows = self.db.query("SELECT * FROM kg_node WHERE node_type = ? ORDER BY node_id", [object_type])
        else:
            rows = self.db.query("SELECT * FROM kg_node ORDER BY node_id")
        return [_node(r) for r in rows]

    def all_edges(self, include_disabled: bool = False) -> list[dict]:
        table = "kg_edge" if include_disabled else "kg_edge_active"
        return [{**r, "properties": loads(r["properties"], {})}
                for r in self.db.query(f"SELECT * FROM {table} ORDER BY edge_id")]

    def search_objects(self, query: str, object_type: str | None = None, limit: int | None = None) -> dict:
        """Lexical search over names, synonyms and definitions. Deterministic ranking."""
        if not isinstance(query, str) or not query.strip():
            raise OntologyAccessError("invalid_argument", "query must be a non-empty string.")
        if object_type:
            if self._require_type(object_type)["storage"] != "graph":
                raise OntologyAccessError(
                    "not_a_graph_type", f"'{object_type}' is stored in operational tables; use query_objects.")
        wanted = normalize(query)
        matches = []
        for node in self.all_nodes(object_type):
            names = [node["canonical_name"]] + node["synonyms"]
            if wanted == node["node_id"].lower() or any(normalize(n) == wanted for n in names):
                score, kind = 1.0, "exact"
            elif any(contains_phrase(query, n) for n in names):
                score, kind = 0.9, "phrase"
            elif any(contains_phrase(n, query) for n in names):
                score, kind = 0.8, "partial"
            else:
                name_score = max(overlap(query, n) for n in names)
                query_score = max(overlap(n, query) for n in names)
                definition_score = overlap(node["definition"], query) if node["definition"] else 0.0
                score = round(max(0.7 * min(name_score, 1.0) * (0.5 + 0.5 * query_score), 0.45 * definition_score), 3)
                kind = "lexical"
            if score >= 0.2:
                matches.append({**_brief(node), "definition": node["definition"], "synonyms": node["synonyms"],
                                "score": score, "match_type": kind})
        matches.sort(key=lambda m: (-m["score"], m["node_id"]))
        limit = self._limit(limit, 10)
        return {"query": query, "object_type": object_type, "matches": matches[:limit],
                "total_matches": len(matches), "truncated": len(matches) > limit,
                "provenance": {"node_ids": [m["node_id"] for m in matches[:limit]], "edge_ids": []}}

    def query_objects(self, object_type: str, filters: dict[str, Any] | None = None, limit: int | None = None) -> dict:
        """Filter instances of one object type. Works for graph and operational types."""
        type_info = self._require_type(object_type)
        filters = filters or {}
        if not isinstance(filters, dict):
            raise OntologyAccessError("invalid_argument", "filters must be an object of field -> value.")
        limit = self._limit(limit)

        if type_info["storage"] == "operational":
            columns = [r["column_name"] for r in self.db.query(
                "SELECT column_name FROM information_schema.columns WHERE table_name = ? ORDER BY ordinal_position",
                [type_info["table"]])]
            unknown = [k for k in filters if k not in columns]
            if unknown:
                raise OntologyAccessError("unknown_field", f"Unknown fields for {object_type}: {unknown}", valid=columns)
            clauses, params = [], []
            for key, value in filters.items():
                if isinstance(value, list):
                    clauses.append(f"{key} IN ({', '.join('?' for _ in value)})")
                    params += value
                else:
                    clauses.append(f"{key} = ?")
                    params.append(value)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            total = self.db.scalar(f"SELECT COUNT(*) FROM {type_info['table']} {where}", params)
            rows = self.db.query(
                f"SELECT * FROM {type_info['table']} {where} ORDER BY {type_info['key_column']} LIMIT {limit}", params)
            return {"object_type": object_type, "storage": "operational", "table": type_info["table"],
                    "objects": rows, "total_matches": total, "truncated": total > limit,
                    "provenance": {"node_ids": [], "edge_ids": []}}

        base_fields = {"node_id", "canonical_name", "definition", "source"}
        allowed = base_fields | {p["name"] for p in type_info["properties"]}
        unknown = [k for k in filters if k not in allowed]
        if unknown:
            raise OntologyAccessError("unknown_field", f"Unknown fields for {object_type}: {unknown}",
                                      valid=sorted(allowed))

        def keep(node: dict) -> bool:
            for key, wanted in filters.items():
                actual = node[key] if key in base_fields else node["properties"].get(key)
                options = wanted if isinstance(wanted, list) else [wanted]
                if not any(_same(actual, option) for option in options):
                    return False
            return True

        nodes = [n for n in self.all_nodes(object_type) if keep(n)]
        return {"object_type": object_type, "storage": "graph", "objects": nodes[:limit],
                "total_matches": len(nodes), "truncated": len(nodes) > limit,
                "provenance": {"node_ids": [n["node_id"] for n in nodes[:limit]], "edge_ids": []}}

    # ------------------------------------------------------------- traversal

    def get_relationship_types(self, object_type: str) -> dict:
        """Which relationships can start or end at this object type, with live edge counts."""
        self._require_type(object_type)
        outgoing, incoming = [], []
        for rel in self.relationship_types().values():
            if rel["disabled"]:
                continue
            entry = {k: rel[k] for k in ("name", "description", "source_types", "target_types", "hierarchical",
                                         "storage", "edge_count")}
            if object_type in rel["source_types"]:
                outgoing.append(entry)
            if object_type in rel["target_types"]:
                incoming.append(entry)
        return {"object_type": object_type, "outgoing": outgoing, "incoming": incoming}

    def _adjacency(self, direction: str, relationship_types: list[str] | None) -> tuple[str, list]:
        if direction not in DIRECTIONS:
            raise OntologyAccessError("invalid_argument", f"direction must be one of {DIRECTIONS}.", direction=direction)
        rel_filter, params = "", []
        if relationship_types:
            rel_filter = f"WHERE relationship_type IN ({', '.join('?' for _ in relationship_types)})"
            params = list(relationship_types)
        parts, all_params = [], []
        if direction in ("out", "both"):
            parts.append("SELECT edge_id, relationship_type, source_node_id AS from_id, target_node_id AS to_id, "
                         f"'out' AS direction, properties, confidence FROM kg_edge_active {rel_filter}")
            all_params += params
        if direction in ("in", "both"):
            parts.append("SELECT edge_id, relationship_type, target_node_id AS from_id, source_node_id AS to_id, "
                         f"'in' AS direction, properties, confidence FROM kg_edge_active {rel_filter}")
            all_params += params
        return " UNION ALL ".join(parts), all_params

    def get_neighbors(self, object_id: str, relationship_type: str | None = None, direction: str = "both",
                      limit: int | None = None) -> dict:
        start = self.get_object(object_id)
        rels = self._require_relationships([relationship_type] if relationship_type else None)
        adjacency, params = self._adjacency(direction, rels)
        rows = self.db.query(
            f"SELECT a.*, n.node_type, n.canonical_name FROM ({adjacency}) a JOIN kg_node n ON n.node_id = a.to_id "
            "WHERE a.from_id = ? ORDER BY a.relationship_type, a.direction, a.edge_id", params + [object_id])
        limit = self._limit(limit)
        neighbors = [{
            "node_id": r["to_id"], "node_type": r["node_type"], "canonical_name": r["canonical_name"],
            "relationship_type": r["relationship_type"], "direction": r["direction"], "edge_id": r["edge_id"],
            "edge_properties": loads(r["properties"], {}), "confidence": r["confidence"],
        } for r in rows]
        shown = neighbors[:limit]
        return {"object": _brief(start), "neighbors": shown, "total_neighbors": len(neighbors),
                "truncated": len(neighbors) > limit,
                "provenance": {"node_ids": [object_id] + [n["node_id"] for n in shown],
                               "edge_ids": [n["edge_id"] for n in shown],
                               "relationship_types": sorted({n["relationship_type"] for n in shown})}}

    def _bfs(self, start_id: str, relationship_types: list[str] | None, max_depth: int, direction: str) -> tuple[dict, list]:
        """Breadth-first reachability in SQL. Returns ({node_id: depth}, tree-candidate edges).

        Cycle-safe: the recursive step is bounded by depth and UNION removes repeats,
        and each node is then kept at its minimum depth only.
        """
        adjacency, params = self._adjacency(direction, relationship_types)
        cte = (
            f"WITH RECURSIVE adj AS ({adjacency}), "
            "walk(node_id, depth) AS ("
            "  SELECT CAST(? AS VARCHAR), 0 "
            "  UNION "
            "  SELECT a.to_id, w.depth + 1 FROM walk w JOIN adj a ON a.from_id = w.node_id WHERE w.depth < ?"
            "), reached AS (SELECT node_id, MIN(depth) AS depth FROM walk GROUP BY node_id) "
        )
        base_params = params + [start_id, max_depth]
        depths = {r["node_id"]: r["depth"] for r in self.db.query(
            cte + "SELECT node_id, depth FROM reached ORDER BY depth, node_id", base_params)}
        edges = self.db.query(
            cte + "SELECT a.edge_id, a.relationship_type, a.from_id, a.to_id, a.direction, a.properties, a.confidence "
            "FROM adj a JOIN reached f ON f.node_id = a.from_id JOIN reached t ON t.node_id = a.to_id "
            "WHERE t.depth = f.depth + 1 ORDER BY t.depth, a.to_id, a.edge_id, a.direction", base_params)
        return depths, edges

    def _depth(self, max_depth: int | None) -> int:
        value = self.limits.default_depth if max_depth is None else max_depth
        if not isinstance(value, int) or value < 1:
            raise OntologyAccessError("invalid_argument", "max_depth must be a positive integer.", max_depth=max_depth)
        return min(value, self.limits.max_depth)

    def traverse_graph(self, start_node_id: str, relationship_types: list[str] | None = None,
                       max_depth: int | None = None, direction: str = "out", limit: int | None = None) -> dict:
        """Everything reachable from a node within max_depth hops, with one shortest path per node."""
        start = self.get_object(start_node_id)
        rels = self._require_relationships(relationship_types)
        depth = self._depth(max_depth)
        depths, edges = self._bfs(start_node_id, rels, depth, direction)

        parent: dict[str, dict] = {}
        for edge in edges:  # ordered, so the first edge into each node is the deterministic choice
            parent.setdefault(edge["to_id"], edge)
        reached_ids = [n for n in depths if n != start_node_id]
        limit = self._limit(limit)
        shown_ids = reached_ids[:limit]
        info = self.get_objects(shown_ids)
        nodes = []
        for node_id in shown_ids:
            node_path, edge_path, rel_path = [node_id], [], []
            cursor = node_id
            while cursor != start_node_id:
                edge = parent[cursor]
                edge_path.insert(0, edge["edge_id"])
                rel_path.insert(0, edge["relationship_type"] if edge["direction"] == "out" else f"~{edge['relationship_type']}")
                cursor = edge["from_id"]
                node_path.insert(0, cursor)
            nodes.append({**info[node_id], "depth": depths[node_id],
                          "path": {"node_ids": node_path, "edge_ids": edge_path, "relationship_types": rel_path}})
        shown = set(shown_ids) | {start_node_id}
        shown_edges = [{
            "edge_id": e["edge_id"], "relationship_type": e["relationship_type"], "from": e["from_id"],
            "to": e["to_id"], "direction": e["direction"], "properties": loads(e["properties"], {}),
        } for e in edges if e["from_id"] in shown and e["to_id"] in shown]
        return {
            "start": _brief(start), "direction": direction, "relationship_types": rels or "all",
            "max_depth": depth, "nodes": nodes, "edges": shown_edges,
            "total_reached": len(reached_ids), "truncated": len(reached_ids) > limit,
            "provenance": {"node_ids": [start_node_id] + shown_ids,
                           "edge_ids": sorted({e["edge_id"] for e in shown_edges}),
                           "relationship_types": sorted({e["relationship_type"] for e in shown_edges}),
                           "paths": [n["path"] for n in nodes]},
        }

    def find_path(self, source_node_id: str, target_node_id: str, relationship_types: list[str] | None = None,
                  max_depth: int | None = None, direction: str = "both") -> dict:
        """One shortest path between two nodes, or found=false within the depth limit."""
        source, target = self.get_object(source_node_id), self.get_object(target_node_id)
        rels = self._require_relationships(relationship_types)
        depth = self._depth(max_depth if max_depth is not None else self.limits.max_depth)
        base = {"source": _brief(source), "target": _brief(target), "max_depth": depth, "direction": direction}
        if source_node_id == target_node_id:
            return {**base, "found": True, "length": 0, "steps": [],
                    "provenance": {"node_ids": [source_node_id], "edge_ids": [], "relationship_types": [],
                                   "paths": [{"node_ids": [source_node_id], "edge_ids": [], "relationship_types": []}]}}
        depths, edges = self._bfs(source_node_id, rels, depth, direction)
        if target_node_id not in depths:
            return {**base, "found": False, "length": None, "steps": [],
                    "provenance": {"node_ids": [source_node_id, target_node_id], "edge_ids": [],
                                   "relationship_types": [], "paths": []}}
        parent: dict[str, dict] = {}
        for edge in edges:
            parent.setdefault(edge["to_id"], edge)
        chain, cursor = [], target_node_id
        while cursor != source_node_id:
            edge = parent[cursor]
            chain.insert(0, edge)
            cursor = edge["from_id"]
        info = self.get_objects([source_node_id] + [e["to_id"] for e in chain])
        steps = [{
            "from": info[e["from_id"]], "relationship_type": e["relationship_type"], "direction": e["direction"],
            "edge_id": e["edge_id"], "edge_properties": loads(e["properties"], {}), "to": info[e["to_id"]],
        } for e in chain]
        node_ids = [source_node_id] + [e["to_id"] for e in chain]
        return {**base, "found": True, "length": len(chain), "steps": steps,
                "provenance": {"node_ids": node_ids, "edge_ids": [e["edge_id"] for e in chain],
                               "relationship_types": sorted({e["relationship_type"] for e in chain}),
                               "paths": [{"node_ids": node_ids, "edge_ids": [e["edge_id"] for e in chain],
                                          "relationship_types": [e["relationship_type"] for e in chain]}]}}

    # ---------------------------------------------------------------- writes

    def _check_properties(self, where: str, values: dict, defs: list[dict]) -> None:
        """Instance data must satisfy the schema: declared properties, right types, required present."""
        types = {"string": (str,), "number": (int, float), "boolean": (bool,), "list": (list,), "object": (dict,)}
        known = {d["name"]: d for d in defs}
        problems = [f"missing required property '{d['name']}'" for d in defs if d.get("required") and values.get(d["name"]) is None]
        for name, value in values.items():
            if name not in known:
                problems.append(f"property '{name}' is not defined in the schema")
            elif value is not None and (not isinstance(value, types[known[name]["type"]])
                                        or (known[name]["type"] == "number" and isinstance(value, bool))):
                problems.append(f"property '{name}' should be {known[name]['type']}")
        if problems:
            raise OntologyAccessError("schema_violation", f"{where}: " + "; ".join(problems), valid=sorted(known))

    def add_node(self, node_id: str, node_type: str, name: str, definition: str = "", synonyms: list[str] | None = None,
                 properties: dict | None = None, source: str = "runtime") -> dict:
        """Append an instance. Rejected unless it conforms to the ontology schema."""
        type_info = self._require_type(node_type)
        if type_info["storage"] != "graph":
            raise OntologyAccessError("not_a_graph_type", f"'{node_type}' instances live in operational tables.")
        if self.db.scalar("SELECT COUNT(*) FROM kg_node WHERE node_id = ?", [node_id]):
            raise OntologyAccessError("duplicate_object", f"An object with id '{node_id}' already exists.")
        properties = properties or {}
        self._check_properties(f"{node_type} {node_id}", properties, type_info["properties"])
        self.db.insert_many("kg_node", [{
            "node_id": node_id, "node_type": node_type, "canonical_name": name, "definition": definition,
            "synonyms": dumps(synonyms or []), "properties": dumps(properties), "source": source,
            "ontology_version": self.meta()["version"]}])
        return self.get_object(node_id)

    def add_edge(self, source_id: str, relationship_type: str, target_id: str, properties: dict | None = None,
                 confidence: float = 1.0, source: str = "runtime") -> str:
        """Append a relationship. Rejected unless the schema allows it between these two object types."""
        rels = self.relationship_types()
        if relationship_type not in rels:
            raise OntologyAccessError("unknown_relationship_type", f"Unknown relationship type '{relationship_type}'.", valid=sorted(rels))
        rel = rels[relationship_type]
        start, end = self.get_object(source_id), self.get_object(target_id)
        if start["node_type"] not in rel["source_types"] or end["node_type"] not in rel["target_types"]:
            raise OntologyAccessError(
                "schema_violation", f"{relationship_type} cannot link {start['node_type']} to {end['node_type']}.",
                allowed_sources=rel["source_types"], allowed_targets=rel["target_types"])
        properties = properties or {}
        self._check_properties(f"{relationship_type} edge", properties, rel["properties"])
        edge_id = f"R{uuid.uuid4().hex[:10]}"
        self.db.insert_many("kg_edge", [{
            "edge_id": edge_id, "source_node_id": source_id, "target_node_id": target_id,
            "relationship_type": relationship_type, "properties": dumps(properties), "source": source,
            "confidence": confidence, "ontology_version": self.meta()["version"]}])
        return edge_id

    def update_properties(self, node_id: str, changes: dict) -> dict:
        node = self.get_object(node_id)
        merged = {**node["properties"], **changes}
        self._check_properties(f"{node['node_type']} {node_id}", merged, self._require_type(node["node_type"])["properties"])
        self.db.execute("UPDATE kg_node SET properties = ? WHERE node_id = ?", [dumps(merged), node_id])
        return self.get_object(node_id)

    # --------------------------------------------------------------- helpers

    def hierarchy(self, object_id: str) -> dict:
        """Ancestors and descendants along the schema's hierarchical relationships."""
        node = self.get_object(object_id)
        rels = [r for r in self.hierarchical_relationships()
                if node["node_type"] in self.relationship_types()[r]["source_types"]]
        if not rels:
            return {"object": _brief(node), "hierarchical_relationships": [], "ancestors": [], "descendants": [],
                    "provenance": {"node_ids": [object_id], "edge_ids": []}}
        up = self.traverse_graph(object_id, rels, self.limits.max_depth, "out", self.limits.max_results)
        down = self.traverse_graph(object_id, rels, self.limits.max_depth, "in", self.limits.max_results)
        return {
            "object": _brief(node), "hierarchical_relationships": rels,
            "ancestors": [{k: n[k] for k in ("node_id", "node_type", "canonical_name", "depth")} for n in up["nodes"]],
            "descendants": [{k: n[k] for k in ("node_id", "node_type", "canonical_name", "depth")} for n in down["nodes"]],
            "provenance": {
                "node_ids": sorted(set(up["provenance"]["node_ids"]) | set(down["provenance"]["node_ids"])),
                "edge_ids": sorted(set(up["provenance"]["edge_ids"]) | set(down["provenance"]["edge_ids"])),
                "relationship_types": rels,
            },
        }


def _brief(node: dict) -> dict:
    return {"node_id": node["node_id"], "node_type": node["node_type"], "canonical_name": node["canonical_name"]}


def _same(actual: Any, wanted: Any) -> bool:
    if isinstance(actual, str) and isinstance(wanted, str):
        return normalize(actual) == normalize(wanted)
    return actual == wanted
