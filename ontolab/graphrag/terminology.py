"""Curated terminology mappings: external, configurable and versioned.

Three kinds of mapping:
  authoritative - the phrase means exactly these concepts
  ambiguous     - the phrase could mean any of several concepts; ask the customer
  collection    - the phrase names a business grouping of several concepts
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from ..ontology.store import OntologyStore
from ..text import contains_phrase, overlap


class Mapping(BaseModel):
    term: str
    variants: list[str] = Field(default_factory=list)
    kind: Literal["authoritative", "ambiguous", "collection"]
    targets: list[str]
    note: str = ""
    clarification: str = ""


class TerminologyFile(BaseModel):
    version: str
    description: str = ""
    mappings: list[Mapping]


class Terminology:
    def __init__(self, path: str | Path, store: OntologyStore):
        self.path = Path(path)
        self.store = store
        self.reload()

    def reload(self) -> None:
        data = TerminologyFile.model_validate(yaml.safe_load(self.path.read_text(encoding="utf-8")))
        self.version = data.version
        self.description = data.description
        self.mappings = data.mappings

    def _targets(self, mapping: Mapping) -> tuple[list[dict], list[str]]:
        known = self.store.get_objects(mapping.targets)
        return [known[t] for t in mapping.targets if t in known], [t for t in mapping.targets if t not in known]

    def resolve(self, text: str) -> dict:
        matches = []
        for mapping in self.mappings:
            phrases = [mapping.term] + mapping.variants
            exact = any(contains_phrase(text, p) or contains_phrase(p, text) for p in phrases)
            score = 1.0 if exact else max(overlap(text, p) for p in phrases)
            if score >= 0.8:
                targets, missing = self._targets(mapping)
                matches.append({
                    "term": mapping.term, "kind": mapping.kind, "score": round(score, 2), "targets": targets,
                    "targets_missing_from_ontology": missing, "note": mapping.note,
                    "clarification_question": mapping.clarification if mapping.kind == "ambiguous" else "",
                    "needs_clarification": mapping.kind == "ambiguous",
                })
        matches.sort(key=lambda m: (-m["score"], m["term"]))
        return {"text": text, "mapping_version": self.version, "matches": matches,
                "provenance": {"node_ids": sorted({t["node_id"] for m in matches for t in m["targets"]}), "edge_ids": []}}

    def target_types(self) -> list[str]:
        return sorted({t["node_type"] for m in self.mappings for t in self._targets(m)[0]})

    def render_for_prompt(self) -> str:
        lines = [f"Curated terminology mappings (version {self.version}). They override your own reading of these phrases:"]
        for mapping in self.mappings:
            targets, _ = self._targets(mapping)
            names = ", ".join(f"{t['canonical_name']} ({t['node_id']})" for t in targets)
            phrases = " / ".join(f'"{p}"' for p in [mapping.term] + mapping.variants)
            if mapping.kind == "authoritative":
                lines.append(f"- {phrases} means: {names}.")
            elif mapping.kind == "collection":
                lines.append(f"- {phrases} means this whole group: {names}. {mapping.note}".rstrip())
            else:
                lines.append(f"- {phrases} is AMBIGUOUS between: {names}. Do not pick one; ask: {mapping.clarification}")
        return "\n".join(lines)

    def catalog(self) -> dict:
        return {"version": self.version, "description": self.description, "path": str(self.path),
                "mappings": [{**m.model_dump(), "targets_resolved": self._targets(m)[0]} for m in self.mappings]}
