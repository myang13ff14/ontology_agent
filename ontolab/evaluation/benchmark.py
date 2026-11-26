"""Benchmark tasks: the question, what answering it requires, and what a correct result is.

Expected results live here and only here. Nothing in prompts, tools or agent code
reads this file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

CATEGORIES = [
    "single_object", "synonym_resolution", "hierarchy", "multi_hop", "relationship_discovery",
    "operational_aggregation", "combined_ontology_sql", "conditional_workflow", "ambiguous_terminology",
    "unsupported", "missing_information", "conflicting_knowledge",
]


class Requirements(BaseModel):
    objects: list[str] = Field(default_factory=list)        # ontology object types that must be reachable
    relationships: list[str] = Field(default_factory=list)  # relationship types that must be readable
    operations: list[str] = Field(default_factory=list)     # operations that must be available
    data: list[str] = Field(default_factory=list)           # metrics, dimensions or properties that must exist


class ReferenceQuery(BaseModel):
    """A semantic-view query whose live result defines the expected numbers."""
    query: dict
    check: Literal["values", "top_dimension"] = "values"
    dimension: str | None = None
    top_n: int = 1


class Expected(BaseModel):
    behavior: Literal["answer", "abstain", "clarify"] = "answer"
    answer_ids: list[str] = Field(default_factory=list)      # ontology objects the answer must name
    min_recall: float = 1.0                                   # share of required items that must be present
    answer_text: list[list[str]] = Field(default_factory=list)  # each group: any one phrase must appear
    forbidden_ids: list[str] = Field(default_factory=list)   # objects that must not be named
    max_extra: int | None = None                              # other same-type objects tolerated (default: len(answer_ids) + 3)
    evidence_ids: list[str] | None = None                     # ids that must appear in tool results (default: answer_ids)
    evidence_edges: list[list[str]] = Field(default_factory=list)  # [source, relationship, target] the ontology must hold
    reference_queries: list[ReferenceQuery] = Field(default_factory=list)
    notes: str = ""


class Task(BaseModel):
    id: str
    split: Literal["dev", "test"]
    category: str
    complexity: int = Field(ge=1, le=4)
    question: str
    required: Requirements = Field(default_factory=Requirements)
    acceptable_sequences: list[list[str]] = Field(default_factory=list)
    argument_checks: dict[str, list[str]] = Field(default_factory=dict)  # tool -> strings one call's arguments must contain
    expected: Expected = Field(default_factory=Expected)
    ontology_gap: Literal["knowledge", "relationship"] | None = None  # the ontology deliberately lacks what is asked

    def evidence_ids(self) -> list[str]:
        return self.expected.answer_ids if self.expected.evidence_ids is None else self.expected.evidence_ids


def load_benchmark(path: str | Path) -> list[Task]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    library: dict[str, list[str]] = raw.get("sequence_library", {})
    tasks = []
    for item in raw["tasks"]:
        sequences = []
        for sequence in item.get("acceptable_sequences", []):
            sequences.append(library[sequence] if isinstance(sequence, str) else sequence)
        task = Task.model_validate({**item, "acceptable_sequences": sequences})
        if task.category not in CATEGORIES:
            raise ValueError(f"task {task.id}: unknown category '{task.category}'")
        tasks.append(task)
    ids = [t.id for t in tasks]
    if len(ids) != len(set(ids)):
        raise ValueError("benchmark has duplicate task ids")
    return tasks


def select(tasks: list[Task], split: str | None = None, ids: list[str] | None = None, limit: int | None = None) -> list[Task]:
    chosen = [t for t in tasks if (split in (None, "all") or t.split == split) and (not ids or t.id in ids)]
    return chosen[:limit] if limit else chosen
