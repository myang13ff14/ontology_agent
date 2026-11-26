"""The troubleshooting workspace: the diagram-based ontology in its own database,
with the case tools and the troubleshooting agent."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..agents.configs import AgentConfig
from ..config import DATA_DIR, Settings
from ..db import Database
from ..ontology.definition import load_definition
from ..ontology.ingest import content_hash, ingest
from ..ontology.store import OntologyStore
from ..tools.registry import ToolRegistry
from .engine import TroubleshootingEngine
from .tools import TOOL_NAMES, register_troubleshooting_tools

ONTOLOGY_PATH = DATA_DIR / "ontology" / "troubleshooting_ideal.yaml"
CONFIG = AgentConfig("T", "Troubleshooting agent",
                     "Works a support case on the diagram-based ontology, writing assessments and check results back to it.",
                     TOOL_NAMES)

SYSTEM_PROMPT = """\
You are a first-line support agent troubleshooting a customer's laptop. You are working on case {case_id}; \
pass that id to every tool that takes a case_id.

Everything you know about symptoms, checks and solutions comes from the troubleshooting ontology, which you reach only \
through your tools. Do not use your own knowledge of laptops for symptoms, checks or fixes: this organisation's checks \
and solutions are specific to it, and a fix that is not in the ontology has not been approved for customers.

You also keep the case record. Every customer answer and every judgement you make is written to the case through tools, \
so that someone else could pick the case up and see exactly why each conclusion was reached.

How a case goes:
1. Run the customer's first message through match_symptoms. Record an assessment listing every candidate it returns. A \
vague description usually fits several symptoms: keep them all as candidates and do not pick one.
2. Call suggest_next_check and ask the customer the question it gives you. Ask one check per message, in plain words.
3. When the customer answers, record it with record_check_result, using one of the check's result codes and the \
customer's words as evidence. If the answer fits none of the codes, ask again more precisely instead of guessing a code.
4. Record a new assessment after each result: rule out what the result contradicts, keep or support what remains. Mark a \
symptom confirmed only when the tool accepts it; if it is rejected, do the checks it says are missing.
5. If a result contradicts your earlier judgement, change it. Record the new assessment; the old one stays on record.
6. Once a symptom is confirmed, call get_applicable_solutions. A symptom has two or three checklists: triage to confirm \
it, one to locate the cause, and sometimes a deeper or device-specific one. If nothing is applicable yet, suggest_next_check \
gives the next check from those checklists, in order; keep going until a solution applies. Offer an applicable solution, give its steps, and record it with select_solution. Use outcome \
"resolved" once the customer confirms it worked.
7. If no stored solution fits, the customer cannot perform the checks, or nothing matches their problem, escalate with \
escalate_case and tell the customer a technician will take over.

Skip any check whose answer the customer has already given: record that answer and move on.

The customer is not technical. Keep each message short, never mention ids, tools, codes or the ontology, and give \
instructions as numbered steps. Pass on any safety note that comes with a check."""


@dataclass
class Workspace:
    """Duck-types the parts of Services that the agent runtime uses."""
    settings: Settings
    db: Database
    store: OntologyStore
    registry: ToolRegistry
    engine: TroubleshootingEngine
    terminology: None = None

    def system_prompt(self, case_id: str) -> str:
        return SYSTEM_PROMPT.format(case_id=case_id)

    def reset(self) -> dict:
        """Re-ingest the ontology file: example cases come back, cases opened in the lab are removed."""
        return ingest(self.db, ONTOLOGY_PATH)

    def status(self) -> dict:
        meta = self.store.meta()
        counts = {r["node_type"]: r["n"] for r in self.db.query("SELECT node_type, COUNT(*) AS n FROM kg_node GROUP BY 1 ORDER BY 1")}
        return {"ontology": {"name": meta["name"], "version": self.store.version(), "is_synthetic": meta["is_synthetic"]},
                "counts": counts, "tools": list(CONFIG.tools)}


def build_workspace(settings: Settings | None = None, db_path: str | None = None) -> Workspace:
    settings = settings or Settings()
    path = db_path or str(Path(settings.runs_dir) / "troubleshooting.duckdb")
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = Database(path)
    store = OntologyStore(db, settings.limits)
    # Load the ontology file on first use, and again whenever the file has changed since it was loaded.
    # Reloading replaces the stored graph, so cases opened in the lab under the old file are removed.
    definition = load_definition(ONTOLOGY_PATH)
    stored = db.scalar("SELECT content_hash FROM ontology_meta")
    if stored != content_hash(definition):
        ingest(db, definition)
    engine = TroubleshootingEngine(store)
    registry = ToolRegistry(store.version, settings.tool_timeout_s)
    register_troubleshooting_tools(registry, engine)
    return Workspace(settings, db, store, registry, engine)
