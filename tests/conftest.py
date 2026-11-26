import os
import tempfile

import pytest

os.environ["ONTOLAB_RUNS_DIR"] = tempfile.mkdtemp(prefix="ontolab-test-runs-")
for name in ("LLM_PROVIDER", "LLM_MODEL", "LLM_API_KEY", "LLM_BASE_URL", "EMBEDDING_MODEL"):
    os.environ.pop(name, None)
os.environ["LLM_PROVIDER"] = "mock"
os.environ["ONTOLAB_SKIP_ENV_FILE"] = "1"  # tests never read real keys or call a real model

from ontolab.agents.runtime import AgentRuntime  # noqa: E402
from ontolab.config import Settings  # noqa: E402
from ontolab.evaluation.runner import Evaluation  # noqa: E402
from ontolab.llm.mock import MockLLM  # noqa: E402
from ontolab.services import build_services  # noqa: E402


@pytest.fixture(scope="session")
def services():
    """Everything built once in an in-memory database (ontology, 600 synthetic cases, profiles)."""
    return build_services(Settings(case_count=600), db_path=":memory:")


@pytest.fixture(scope="session")
def store(services):
    return services.store


@pytest.fixture(scope="session")
def registry(services):
    return services.registry


@pytest.fixture(scope="session")
def runtime(services):
    return AgentRuntime(services, MockLLM())


@pytest.fixture(scope="session")
def evaluation(services, runtime):
    return Evaluation(services, runtime)


@pytest.fixture()
def restore_graph(store):
    """Undo any relationship hiding a test did."""
    yield
    store.set_disabled([])
