"""Runtime settings, read from the environment (and a .env file if present)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"


def load_env_file(path: Path = ROOT / ".env") -> None:
    """Minimal .env reader. Real environment variables win. Set ONTOLAB_SKIP_ENV_FILE=1 to ignore the file."""
    if os.environ.get("ONTOLAB_SKIP_ENV_FILE") or not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class Limits:
    """Bounds applied to every graph and retrieval operation."""

    max_depth: int = 4  # hard ceiling for traverse_graph / find_path
    default_depth: int = 2
    max_results: int = 200  # hard ceiling on rows / nodes returned by one call
    default_results: int = 25
    profile_top_k: int = 5


@dataclass(frozen=True)
class Settings:
    db_path: str = field(default_factory=lambda: os.environ.get("ONTOLAB_DB", str(ROOT / "runs" / "ontolab.duckdb")))
    ontology_path: str = field(
        default_factory=lambda: os.environ.get("ONTOLAB_ONTOLOGY", str(DATA_DIR / "ontology" / "laptop_support.yaml")))
    semantic_view_path: str = field(
        default_factory=lambda: os.environ.get("ONTOLAB_SEMANTIC_VIEW", str(DATA_DIR / "semantic_view.yaml")))
    terminology_path: str = field(
        default_factory=lambda: os.environ.get("ONTOLAB_TERMINOLOGY", str(DATA_DIR / "terminology_mappings.yaml")))
    benchmark_path: str = field(
        default_factory=lambda: os.environ.get("ONTOLAB_BENCHMARK", str(DATA_DIR / "benchmark" / "questions.yaml")))
    runs_dir: str = field(default_factory=lambda: os.environ.get("ONTOLAB_RUNS_DIR", str(ROOT / "runs")))
    seed: int = field(default_factory=lambda: int(os.environ.get("ONTOLAB_SEED", "42")))
    case_count: int = field(default_factory=lambda: int(os.environ.get("ONTOLAB_CASES", "2000")))
    max_tool_calls: int = field(default_factory=lambda: int(os.environ.get("ONTOLAB_MAX_TOOL_CALLS", "12")))
    tool_timeout_s: float = field(default_factory=lambda: float(os.environ.get("ONTOLAB_TOOL_TIMEOUT_S", "10")))
    run_timeout_s: float = field(default_factory=lambda: float(os.environ.get("ONTOLAB_RUN_TIMEOUT_S", "180")))
    memory_turns: int = field(default_factory=lambda: int(os.environ.get("ONTOLAB_MEMORY_TURNS", "6")))
    memory_ttl_s: float = field(default_factory=lambda: float(os.environ.get("ONTOLAB_MEMORY_TTL_S", "3600")))
    limits: Limits = field(default_factory=Limits)
