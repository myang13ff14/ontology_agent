"""Run the benchmark across agent configurations and aggregate the three evaluation
dimensions: tool coverage, tool-calling accuracy, end-to-end performance.

Results are written to runs/eval_<id>.json. Every result file records which LLM
produced it; mock-LLM runs are flagged so they are never mistaken for a model
evaluation.
"""

from __future__ import annotations

import concurrent.futures
import json
import threading
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

from ..agents.configs import CONFIGS
from ..agents.runtime import AgentRuntime
from ..llm.base import LLMClient
from ..llm.factory import describe
from ..services import Services
from .benchmark import Task, load_benchmark, select
from .coverage import CoverageAnalyzer
from .scoring import FAILURE_CATEGORIES, Scorer

STORED_RESULT_CHARS = 6000


def _avg(values: list) -> float | None:
    values = [v for v in values if v is not None]
    return round(mean(values), 4) if values else None


def _trim_trace(trace: dict) -> dict:
    """Keep traces inspectable without storing megabytes of tool output."""
    slim = {k: v for k, v in trace.items() if k != "tool_calls"}
    slim["tool_calls"] = []
    for call in trace["tool_calls"]:
        call = dict(call)
        text = json.dumps(call.get("result"), default=str)
        if len(text) > STORED_RESULT_CHARS:
            call["result"] = {"truncated_for_storage": True, "preview": text[:STORED_RESULT_CHARS]}
        slim["tool_calls"].append(call)
    return slim


class Evaluation:
    def __init__(self, services: Services, runtime: AgentRuntime):
        self.services = services
        self.runtime = runtime
        self.tasks = load_benchmark(services.settings.benchmark_path)
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()

    def analyzer(self) -> CoverageAnalyzer:
        return CoverageAnalyzer(self.services.registry, self.services.store, self.services.semantic)

    def coverage(self, configs: list[str] | None = None, split: str | None = None,
                 disabled_tools: list[str] | None = None, extra_tools: dict[str, list[str]] | None = None) -> dict:
        """Tool coverage per configuration. No LLM involved."""
        analyzer = self.analyzer()
        tasks = select(self.tasks, split)
        out = {}
        for key in configs or list(CONFIGS):
            tools = self.runtime.tools_for(key, disabled_tools, (extra_tools or {}).get(key))
            out[key] = analyzer.report(tasks, tools, CONFIGS[key].prompt_operations)
        return {"split": split or "all", "task_count": len(tasks), "configs": out,
                "note": "Computed from tool capability declarations and the ontology schema; no model is run."}

    # ------------------------------------------------------------- running

    def run(self, configs: list[str] | None = None, split: str | None = "test", repeats: int = 1,
            task_ids: list[str] | None = None, limit: int | None = None, llm: LLMClient | None = None,
            disabled_tools: list[str] | None = None, description_overrides: dict[str, str] | None = None,
            max_tool_calls: int | None = None, concurrency: int = 4, label: str = "",
            progress: callable | None = None) -> dict:
        configs = configs or list(CONFIGS)
        tasks = select(self.tasks, split, task_ids, limit)
        llm = llm or self.runtime.llm()
        analyzer, scorer = self.analyzer(), Scorer(self.services)
        terminology_text = self.services.terminology.render_for_prompt()

        coverage_rows: dict[tuple[str, str], dict] = {}
        coverage_reports = {}
        for key in configs:
            tools = self.runtime.tools_for(key, disabled_tools)
            report = analyzer.report(tasks, tools, CONFIGS[key].prompt_operations)
            coverage_reports[key] = {k: v for k, v in report.items() if k not in ("tasks", "access")}
            for row in report["tasks"]:
                coverage_rows[(key, row["task_id"])] = row

        jobs = [(key, task, repeat) for key in configs for task in tasks for repeat in range(1, repeats + 1)]
        results: list[dict] = []
        done = 0

        def one(job: tuple[str, Task, int]) -> dict:
            key, task, repeat = job
            trace = self.runtime.run(task.question, key, llm=llm, disabled_tools=disabled_tools,
                                     description_overrides=description_overrides, max_tool_calls=max_tool_calls)
            extra = terminology_text if CONFIGS[key].uses_terminology_prompt else ""
            scored = scorer.score(task, trace, coverage_rows[(key, task.id)], extra)
            return {**scored, "repeat": repeat, "category": task.category, "complexity": task.complexity,
                    "question": task.question, "final_answer": trace["final_answer"], "trace": _trim_trace(trace)}

        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
            for result in pool.map(one, jobs):
                results.append(result)
                done += 1
                if progress:
                    progress(done, len(jobs))

        run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        output = {
            "run_id": run_id, "label": label, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "llm": describe(llm), "is_mock": llm.is_mock,
            "notice": ("MOCK LLM RUN: these numbers test the pipeline only. They are not a model evaluation."
                       if llm.is_mock else "Scores come from rule-based text matching; see README for limits."),
            "ontology_version": self.services.store.version(),
            "terminology_version": self.services.terminology.version,
            "profile_version": self.services.profiles.profile_version,
            "settings": {"configs": configs, "split": split or "all", "repeats": repeats, "task_count": len(tasks),
                         "disabled_tools": disabled_tools or [], "description_overrides": sorted(description_overrides or {}),
                         "max_tool_calls": max_tool_calls or self.services.settings.max_tool_calls},
            "coverage": coverage_reports,
            "summary": summarize(results, configs, coverage_reports),
            "results": results,
        }
        path = Path(self.services.settings.runs_dir)
        path.mkdir(parents=True, exist_ok=True)
        (path / f"eval_{run_id}.json").write_text(json.dumps(output, default=str), encoding="utf-8")
        return output

    # -------------------------------------------------- background execution

    def start(self, **kwargs) -> str:
        """Run in a background thread; poll job() for progress."""
        job_id = uuid.uuid4().hex[:10]
        with self._lock:
            self._jobs[job_id] = {"job_id": job_id, "status": "running", "done": 0, "total": 0, "run_id": None, "error": None}

        def progress(done: int, total: int) -> None:
            with self._lock:
                self._jobs[job_id].update(done=done, total=total)

        def work() -> None:
            try:
                output = self.run(progress=progress, **kwargs)
                with self._lock:
                    self._jobs[job_id].update(status="finished", run_id=output["run_id"])
            except Exception as exc:
                with self._lock:
                    self._jobs[job_id].update(status="failed", error=f"{type(exc).__name__}: {exc}")

        threading.Thread(target=work, daemon=True, name=f"eval-{job_id}").start()
        return job_id

    def job(self, job_id: str) -> dict | None:
        with self._lock:
            return dict(self._jobs[job_id]) if job_id in self._jobs else None

    def list_runs(self) -> list[dict]:
        out = []
        for path in sorted(Path(self.services.settings.runs_dir).glob("eval_*.json"), reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            out.append({k: data.get(k) for k in ("run_id", "label", "created_at", "llm", "is_mock", "settings", "ontology_version")})
        return out

    def load_run(self, run_id: str) -> dict | None:
        path = Path(self.services.settings.runs_dir) / f"eval_{run_id}.json"
        if not path.exists() or not run_id.replace("-", "").isalnum():
            return None
        return json.loads(path.read_text(encoding="utf-8"))


# ------------------------------------------------------------- aggregation

def summarize(results: list[dict], configs: list[str], coverage: dict) -> dict:
    return {"by_config": {key: _summarize_config([r for r in results if r["config"] == key], coverage.get(key, {}))
                          for key in configs}}


def _summarize_config(rows: list[dict], coverage: dict) -> dict:
    if not rows:
        return {}
    n = len(rows)
    covered = [r for r in rows if r["covered"]]
    judged = [r for r in covered if r["tool_use"]["tool_use_correct"] is not None]
    correct_use = [r for r in judged if r["tool_use"]["tool_use_correct"]]
    with_evidence = [r for r in rows if r["evidence"]["retrieved"] is True]
    failures = [r for r in rows if not r["success"]]

    by_task: dict[str, list[dict]] = {}
    for r in rows:
        by_task.setdefault(r["task_id"], []).append(r)
    stable = [len({r["success"] for r in runs}) == 1 for runs in by_task.values() if len(runs) > 1]
    sequence_agreement = []
    for runs in by_task.values():
        if len(runs) > 1:
            sequences = Counter(tuple(r["tool_use"]["called"]) for r in runs)
            sequence_agreement.append(sequences.most_common(1)[0][1] / len(runs))

    def group(key: str) -> dict:
        groups: dict = {}
        for r in rows:
            groups.setdefault(r[key], []).append(r)
        return {str(k): {"runs": len(v), "success_rate": round(sum(x["success"] for x in v) / len(v), 4),
                         "mean_score": round(mean(x["score"] for x in v), 4),
                         "coverage_rate": round(sum(x["covered"] for x in v) / len(v), 4)}
                for k, v in sorted(groups.items(), key=lambda kv: str(kv[0]))}

    p_cover = len(covered) / n
    p_use = len(correct_use) / len(judged) if judged else None
    p_interpret = sum(r["success"] for r in correct_use) / len(correct_use) if correct_use else None
    return {
        "runs": n, "tasks": len(by_task), "tool_count": coverage.get("tool_count"),
        "coverage": {k: coverage.get(k) for k in ("task_coverage_rate", "object_access_coverage",
                                                  "relationship_access_coverage", "operation_coverage",
                                                  "direct_tasks", "multi_call_tasks")}
        | {"unsupported_tasks": [u["task_id"] for u in coverage.get("unsupported_tasks", [])],
           "covered_in_practice_runs": sum(r["covered_in_practice"] for r in rows)},
        "tool_calling": {
            "judged_runs": len(judged),
            "tool_selection_accuracy": _avg([r["tool_use"]["tool_selection_accuracy"] for r in judged]),
            "required_tool_recall": _avg([r["tool_use"]["required_tool_recall"] for r in judged]),
            "unnecessary_tool_calls": _avg([r["tool_use"]["unnecessary_tool_calls"] for r in judged]),
            "argument_accuracy": _avg([r["tool_use"]["argument_accuracy"] for r in judged]),
            "sequencing_correctness": _avg([r["tool_use"]["sequencing_correctness"] for r in judged]),
            "multi_step_orchestration_success": _avg([1.0 if r["tool_use"]["multi_step_success"] else 0.0
                                                      for r in judged if r["tool_use"]["multi_step_success"] is not None]),
            "correct_tool_use_rate": round(p_use, 4) if p_use is not None else None,
            "grounded_interpretation_accuracy": round(sum(r["success"] for r in with_evidence) / len(with_evidence), 4)
            if with_evidence else None,
            "alternative_sequences_accepted": sum(1 for r in rows if r["tool_use"]["alternative_sequence"]),
        },
        "end_to_end": {
            "success_rate": round(sum(r["success"] for r in rows) / n, 4),
            "mean_score_out_of_2": round(mean(r["score"] for r in rows), 4),
            "final_answer_accuracy": round(mean(r["score"] for r in rows) / 2, 4),
            "task_completion_rate": round(sum(r["completed"] for r in rows) / n, 4),
            "groundedness": _avg([r["grounding"]["groundedness"] for r in rows]),
            "hallucination_rate": round(sum(r["grounding"]["hallucinated"] for r in rows) / n, 4),
            "avg_tool_calls": round(mean(r["tool_call_count"] for r in rows), 2),
            "avg_input_tokens": round(mean(r["input_tokens"] for r in rows), 1),
            "avg_output_tokens": round(mean(r["output_tokens"] for r in rows), 1),
            "avg_latency_ms": round(mean(r["latency_ms"] for r in rows), 1),
            "run_to_run_consistency": round(sum(stable) / len(stable), 4) if stable else None,
            "tool_sequence_agreement": _avg(sequence_agreement),
        },
        # Task Success = Coverage x Correct Tool Use | Coverage x Successful Interpretation | both.
        # A conditional decomposition of the observed runs, not a claim of independence.
        "decomposition": {
            "p_covered": round(p_cover, 4),
            "p_correct_tool_use_given_covered": round(p_use, 4) if p_use is not None else None,
            "p_success_given_correct_tool_use": round(p_interpret, 4) if p_interpret is not None else None,
            "product": round(p_cover * p_use * p_interpret, 4) if None not in (p_use, p_interpret) else None,
            "observed_success_rate": round(sum(r["success"] for r in rows) / n, 4),
            "note": "The product covers tasks judged for tool use; abstention tasks and uncovered tasks answered "
                    "correctly make the observed rate differ.",
        },
        "failure_attribution": {
            "failed_runs": len(failures),
            "primary": {c: sum(1 for r in failures if r["failure"]["primary"] == c) for c in FAILURE_CATEGORIES
                        if any(r["failure"]["primary"] == c for r in failures)},
            "contributing": {c: sum(1 for r in failures if any(x["category"] == c for x in r["failure"]["contributing"]))
                             for c in FAILURE_CATEGORIES
                             if any(any(x["category"] == c for x in r["failure"]["contributing"]) for r in failures)},
            "by_layer": dict(Counter(r["failure"]["layer"] for r in failures)),
        },
        "by_category": group("category"),
        "by_complexity": group("complexity"),
    }
