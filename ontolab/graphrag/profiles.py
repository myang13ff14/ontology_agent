"""Flattened GraphRAG: precomputed, denormalised concept profiles.

One profile per ontology node, holding everything an agent would otherwise have
to traverse for at query time: name, definition, synonyms, parents and children,
neighbours, attributes aggregated from all descendants, values inherited from
ancestors, and multi-hop "related knowledge" chains named by the ontology's
profile recipes. Each profile records the node and edge ids it was built from.

Profiles are versioned with the graph they were built from; `is_stale()` tells
when the ontology has changed since the last build.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np

from ..db import Database, dumps, loads
from ..ontology.store import OntologyStore
from ..text import contains_phrase, tokens
from .embedding import bm25_scores, make_embedder

MAX_CHAINS_PER_RECIPE = 80


def _label(node: dict) -> str:
    return f"{node['canonical_name']} ({node['node_id']})"


def _props(properties: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in properties.items() if v not in (None, "", []))


class ProfileBuilder:
    def __init__(self, store: OntologyStore):
        self.store = store
        self.nodes = {n["node_id"]: n for n in store.all_nodes()}
        self.edges = store.all_edges()
        self.hierarchical = set(store.hierarchical_relationships())
        self.recipes = store.profile_recipes()
        self.out: dict[str, list[dict]] = {}
        self.inc: dict[str, list[dict]] = {}
        for e in self.edges:
            self.out.setdefault(e["source_node_id"], []).append(e)
            self.inc.setdefault(e["target_node_id"], []).append(e)

    def _hops(self, node_id: str, hop: str) -> list[tuple[dict, str]]:
        """Edges for one recipe hop. '~REL' follows the relationship backwards."""
        if hop.startswith("~"):
            found = [(e, e["source_node_id"]) for e in self.inc.get(node_id, []) if e["relationship_type"] == hop[1:]]
        else:
            found = [(e, e["target_node_id"]) for e in self.out.get(node_id, []) if e["relationship_type"] == hop]
        return sorted(found, key=lambda pair: (pair[0]["properties"].get("step_order", 0), pair[0]["edge_id"]))

    def _lineage(self, node_id: str, upward: bool) -> list[str]:
        seen, frontier, order = {node_id}, [node_id], []
        while frontier:
            nxt = []
            for current in frontier:
                edges = self.out.get(current, []) if upward else self.inc.get(current, [])
                for e in sorted(edges, key=lambda e: e["edge_id"]):
                    if e["relationship_type"] not in self.hierarchical:
                        continue
                    other = e["target_node_id"] if upward else e["source_node_id"]
                    if other not in seen:
                        seen.add(other)
                        order.append(other)
                        nxt.append(other)
            frontier = nxt
        return order

    def _neighbors(self, node_id: str) -> dict[str, list[dict]]:
        groups: dict[str, list[dict]] = {}
        for direction, edges in (("out", self.out.get(node_id, [])), ("in", self.inc.get(node_id, []))):
            for e in sorted(edges, key=lambda e: (e["properties"].get("step_order", 0), e["edge_id"])):
                if e["relationship_type"] in self.hierarchical:
                    continue
                other = e["target_node_id"] if direction == "out" else e["source_node_id"]
                key = e["relationship_type"] if direction == "out" else f"~{e['relationship_type']}"
                groups.setdefault(key, []).append({
                    "node_id": other, "canonical_name": self.nodes[other]["canonical_name"],
                    "node_type": self.nodes[other]["node_type"], "edge_id": e["edge_id"],
                    "edge_properties": e["properties"],
                })
        return groups

    def _chains(self, start_id: str, path: list[str]) -> list[dict]:
        """Follow a recipe path. Each chain is rendered by its final hop, which keeps
        profiles compact; the leading hops are covered by the shorter recipes before it."""
        chains = [{"node_ids": [start_id], "edge_ids": [], "arrows": []}]
        for hop in path:
            extended = []
            for chain in chains:
                for edge, other in self._hops(chain["node_ids"][-1], hop):
                    note = _props(edge["properties"])
                    arrow = f" -{hop}{'[' + note + ']' if note else ''}-> "
                    extended.append({"node_ids": chain["node_ids"] + [other], "edge_ids": chain["edge_ids"] + [edge["edge_id"]],
                                     "arrows": chain["arrows"] + [arrow]})
            chains = extended
            if not chains:
                return []
        seen, out = set(), []
        for chain in chains:
            ids, arrows = chain["node_ids"][-2:], chain["arrows"][-1:]
            text = _label(self.nodes[ids[0]])
            for arrow, node_id in zip(arrows, ids[1:]):
                text += arrow + _label(self.nodes[node_id])
            note = _props({k: (v if len(str(v)) <= 110 else str(v)[:107] + "...")
                           for k, v in self.nodes[ids[-1]]["properties"].items()})
            if note:
                text += f" {{{note}}}"
            if text not in seen:
                seen.add(text)
                out.append({**chain, "text": text})
        return out[:MAX_CHAINS_PER_RECIPE]

    def build(self, node_id: str) -> dict:
        node = self.nodes[node_id]
        used_nodes, used_edges = {node_id}, set()
        parents = [e["target_node_id"] for e in self.out.get(node_id, []) if e["relationship_type"] in self.hierarchical]
        children = [e["source_node_id"] for e in self.inc.get(node_id, []) if e["relationship_type"] in self.hierarchical]
        ancestors, descendants = self._lineage(node_id, True), self._lineage(node_id, False)
        neighbors = self._neighbors(node_id)

        inherited: dict[str, dict] = {}
        for ancestor in ancestors:  # nearest first
            for key, items in self._neighbors(ancestor).items():
                if not key.startswith("~") and key not in neighbors and key not in inherited:
                    inherited[key] = {"from": ancestor, "items": items}
        aggregated: dict[str, dict[str, dict]] = {}
        for descendant in descendants:
            for key, items in self._neighbors(descendant).items():
                if key.startswith("~"):
                    continue
                for item in items:
                    aggregated.setdefault(key, {}).setdefault(item["node_id"], item)

        chain_groups = []
        anchors = [node_id] + ancestors
        for path in self.recipes.get(node["node_type"], []):
            for anchor in anchors:  # the node itself, else the nearest ancestor that has this knowledge
                chains = self._chains(anchor, path)
                if chains:
                    chain_groups.append({"path": path, "anchor": anchor, "chains": chains})
                    break

        lines = [f"[{node['node_type']}] {_label(node)}"]
        if node["definition"]:
            lines.append(f"Definition: {node['definition']}")
        if node["synonyms"]:
            lines.append("Also called: " + ", ".join(node["synonyms"]))
        if _props(node["properties"]):
            lines.append("Properties: " + _props(node["properties"]))
        if parents:
            lines.append("Parent concepts: " + ", ".join(_label(self.nodes[p]) for p in parents))
        if ancestors:
            lines.append("All ancestors: " + ", ".join(_label(self.nodes[a]) for a in ancestors))
        if children:
            lines.append("Child concepts: " + ", ".join(_label(self.nodes[c]) for c in children))
        if descendants:
            lines.append("All descendants: " + ", ".join(_label(self.nodes[d]) for d in descendants))
        for key, items in neighbors.items():
            lines.append(f"{key}: " + ", ".join(
                _label(self.nodes[i["node_id"]]) + (f" [{_props(i['edge_properties'])}]" if _props(i["edge_properties"]) else "")
                for i in items))
            used_edges.update(i["edge_id"] for i in items)
            used_nodes.update(i["node_id"] for i in items)
        for key, entry in inherited.items():
            lines.append(f"Inherited from {_label(self.nodes[entry['from']])} - {key}: "
                         + ", ".join(_label(self.nodes[i["node_id"]]) for i in entry["items"]))
            used_edges.update(i["edge_id"] for i in entry["items"])
            used_nodes.update(i["node_id"] for i in entry["items"])
        for key, items in aggregated.items():
            lines.append(f"Across all descendants - {key}: " + ", ".join(_label(self.nodes[i]) for i in items))
            used_edges.update(i["edge_id"] for i in items.values())
            used_nodes.update(items)
        for group in chain_groups:
            origin = "" if group["anchor"] == node_id else f" (inherited from {_label(self.nodes[group['anchor']])})"
            lines.append(f"Related knowledge via {' > '.join(group['path'])}{origin}:")
            for chain in group["chains"]:
                lines.append("  " + chain["text"])
                used_nodes.update(chain["node_ids"])
                used_edges.update(chain["edge_ids"])
        used_nodes.update(parents + children + ancestors + descendants)

        structured = {
            "parents": parents, "children": children, "ancestors": ancestors, "descendants": descendants,
            "neighbors": {k: [i["node_id"] for i in v] for k, v in neighbors.items()},
            "inherited": {k: {"from": v["from"], "node_ids": [i["node_id"] for i in v["items"]]} for k, v in inherited.items()},
            "descendant_attributes": {k: sorted(v) for k, v in aggregated.items()},
            "recipe_paths": [{"path": g["path"], "anchor": g["anchor"], "chain_count": len(g["chains"])} for g in chain_groups],
        }
        return {
            "node_id": node_id, "node_type": node["node_type"], "title": node["canonical_name"],
            "synonyms": node["synonyms"], "text": "\n".join(lines), "structured": structured,
            "source_node_ids": sorted(used_nodes), "source_edge_ids": sorted(used_edges),
        }

    def build_all(self) -> list[dict]:
        return [self.build(node_id) for node_id in sorted(self.nodes)]


class ProfileIndex:
    """Hybrid keyword + vector retrieval over the stored profiles."""

    def __init__(self, db: Database, store: OntologyStore):
        self.db = db
        self.store = store
        self.embedder = make_embedder()
        self.profiles: list[dict] = []
        self.vectors = np.zeros((0, 1), dtype=np.float32)
        self.documents: list[list[str]] = []
        self.profile_version: str | None = None

    # ------------------------------------------------------------- building

    def rebuild(self) -> dict:
        """Recompute every profile from the current graph and refresh the index."""
        profiles = ProfileBuilder(self.store).build_all()
        self.embedder = make_embedder()
        self.embedder.fit([p["text"] for p in profiles])
        vectors = self.embedder.embed([p["text"] for p in profiles])
        version = f"{self.store.version()}@{self.store.graph_hash()}"
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        self.db.execute("DELETE FROM kg_profile")
        self.db.insert_many("kg_profile", [{
            "node_id": p["node_id"], "node_type": p["node_type"], "title": p["title"], "text": p["text"],
            "structured": dumps({**p["structured"], "synonyms": p["synonyms"]}),
            "source_node_ids": dumps(p["source_node_ids"]), "source_edge_ids": dumps(p["source_edge_ids"]),
            "embedding": dumps([round(float(x), 5) for x in vector]),
            "ontology_version": self.store.version(), "profile_version": version, "built_at": now,
        } for p, vector in zip(profiles, vectors)])
        self.load()
        return self.status()

    def load(self) -> None:
        rows = self.db.query("SELECT * FROM kg_profile ORDER BY node_id")
        self.profiles = []
        for row in rows:
            structured = loads(row["structured"], {})
            self.profiles.append({
                "node_id": row["node_id"], "node_type": row["node_type"], "title": row["title"], "text": row["text"],
                "synonyms": structured.pop("synonyms", []), "structured": structured,
                "source_node_ids": loads(row["source_node_ids"], []), "source_edge_ids": loads(row["source_edge_ids"], []),
            })
        self.profile_version = rows[0]["profile_version"] if rows else None
        if rows:
            self.embedder.fit([p["text"] for p in self.profiles])
            self.vectors = np.asarray([loads(r["embedding"], []) for r in rows], dtype=np.float32)
        self.documents = [tokens(" ".join([p["title"]] * 3 + p["synonyms"] * 2 + [p["text"]])) for p in self.profiles]

    def is_stale(self) -> bool:
        return self.profile_version != f"{self.store.version()}@{self.store.graph_hash()}"

    def status(self) -> dict:
        return {"profile_count": len(self.profiles), "profile_version": self.profile_version,
                "current_graph_version": f"{self.store.version()}@{self.store.graph_hash()}",
                "stale": self.is_stale(), "embedder": self.embedder.name}

    # ------------------------------------------------------------ retrieval

    def search(self, query: str, top_k: int = 3, object_type: str | None = None, max_chars: int = 9500) -> dict:
        if not self.profiles:
            return {"query": query, "results": [], "profile_version": None, "stale": True,
                    "provenance": {"node_ids": [], "edge_ids": []}}
        keyword = bm25_scores(tokens(query), self.documents)
        vector = (self.vectors @ self.embedder.embed([query])[0]).tolist()
        keyword_rank = {i: r for r, i in enumerate(sorted(range(len(keyword)), key=lambda i: (-keyword[i], i)), 1)}
        vector_rank = {i: r for r, i in enumerate(sorted(range(len(vector)), key=lambda i: (-vector[i], i)), 1)}
        scored = []
        for i, profile in enumerate(self.profiles):
            if object_type and profile["node_type"] != object_type:
                continue
            fused = (1 / (60 + keyword_rank[i]) if keyword[i] > 0 else 0.0) + 1 / (60 + vector_rank[i])
            named = any(contains_phrase(query, name) for name in [profile["title"]] + profile["synonyms"])
            scored.append((fused + (0.05 if named else 0.0), i, named))
        scored.sort(key=lambda item: (-item[0], self.profiles[item[1]]["node_id"]))
        results = []
        for score, i, named in scored[:top_k]:
            p = self.profiles[i]
            text = p["text"] if len(p["text"]) <= max_chars else p["text"][:max_chars] + "\n... (profile truncated)"
            results.append({
                "node_id": p["node_id"], "node_type": p["node_type"], "canonical_name": p["title"],
                "score": round(score, 4), "keyword_score": round(keyword[i], 3), "vector_score": round(vector[i], 3),
                "name_match": named, "profile": text, "descendant_ids": p["structured"].get("descendants", []),
                "source_node_ids": p["source_node_ids"], "source_edge_ids": p["source_edge_ids"],
            })
        return {
            "query": query, "results": results, "profile_version": self.profile_version, "stale": self.is_stale(),
            "retrieval": f"hybrid: BM25 + {self.embedder.name} vectors, reciprocal rank fusion",
            "provenance": {"node_ids": sorted({n for r in results for n in r["source_node_ids"]}),
                           "edge_ids": sorted({e for r in results for e in r["source_edge_ids"]})},
        }
