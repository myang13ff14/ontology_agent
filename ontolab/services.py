"""Builds and wires every layer. One Services object per process."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb

from .config import Settings, load_env_file
from .db import Database
from .graphrag.profiles import ProfileIndex
from .graphrag.terminology import Terminology
from .ontology.ingest import ingest
from .ontology.store import OntologyStore
from .semantic.view import SemanticView
from .tools.domain import register_domain_tools
from .tools.generic import register_generic_tools
from .tools.registry import ToolRegistry
from .tools.retrieval import register_graphrag_tools, register_semantic_tool


class DatabaseBusyError(RuntimeError):
    """Another process holds the DuckDB file."""


@dataclass
class Services:
    settings: Settings
    db: Database
    store: OntologyStore
    semantic: SemanticView
    profiles: ProfileIndex
    terminology: Terminology
    registry: ToolRegistry

    def status(self) -> dict:
        meta = self.store.meta()
        counts = {t: self.db.scalar(f"SELECT COUNT(*) FROM {t}") for t in
                  ("kg_node", "kg_edge", "service_case", "diagnostic_execution", "work_order")}
        return {
            "ontology": {"name": meta["name"], "version": self.store.version(), "base_version": meta["version"],
                         "is_synthetic": meta["is_synthetic"], "disabled": self.store.disabled()},
            "counts": counts, "profiles": self.profiles.status(),
            "terminology_version": self.terminology.version,
            "data_notice": "All ontology content and operational records are synthetic.",
        }


def build_services(settings: Settings | None = None, db_path: str | None = None, rebuild: bool = False) -> Services:
    """Open (or create) the database and make sure every layer is populated.

    With an empty database, or rebuild=True, this ingests the ontology, generates
    the synthetic operational data, installs the semantic views and builds the
    flattened profiles. Otherwise it reuses what is stored.
    """
    load_env_file()
    settings = settings or Settings()
    path = db_path or settings.db_path
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    try:
        db = Database(path)
    except duckdb.IOException as exc:
        raise DatabaseBusyError(
            f"The database at {path} is in use by another process, most likely the running backend. "
            "DuckDB allows one process at a time: stop the backend to use the command line, or use the "
            "web interface / API while it is running.") from exc
    store = OntologyStore(db, settings.limits)

    empty = db.scalar("SELECT COUNT(*) FROM ontology_meta") == 0
    if empty or rebuild:
        from .sample.operational import generate
        ingest(db, settings.ontology_path)
        generate(db, store, settings.case_count, settings.seed)

    semantic = SemanticView(db, settings.semantic_view_path)
    semantic.install()
    profiles = ProfileIndex(db, store)
    if empty or rebuild or db.scalar("SELECT COUNT(*) FROM kg_profile") == 0:
        profiles.rebuild()
    else:
        profiles.load()
    terminology = Terminology(settings.terminology_path, store)

    registry = ToolRegistry(store.version, settings.tool_timeout_s)
    register_semantic_tool(registry, semantic)
    register_domain_tools(registry, store, semantic)
    register_generic_tools(registry, store)
    register_graphrag_tools(registry, profiles, terminology)
    return Services(settings, db, store, semantic, profiles, terminology, registry)
