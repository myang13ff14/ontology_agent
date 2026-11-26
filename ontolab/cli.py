"""Command line entry points.

    python -m ontolab.cli bootstrap [--rebuild]      build the database, profiles and views
    python -m ontolab.cli status
    python -m ontolab.cli ask "question" --config B
    python -m ontolab.cli coverage [--split test]
    python -m ontolab.cli evaluate [--split test] [--repeats 3] [--configs A B C D E] [--mock]
"""

from __future__ import annotations

import argparse
import json
import sys

from .agents.configs import CONFIGS
from .agents.runtime import AgentRuntime
from .evaluation.runner import Evaluation
from .llm.factory import make_llm
from .services import DatabaseBusyError, build_services


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ontolab")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("bootstrap", help="Build the database from the ontology file and synthetic data generator.")
    p.add_argument("--rebuild", action="store_true", help="Discard and regenerate everything.")
    sub.add_parser("status", help="Show what is loaded.")
    p = sub.add_parser("ask", help="Ask one question with one agent configuration.")
    p.add_argument("question")
    p.add_argument("--config", default="B", choices=sorted(CONFIGS))
    p.add_argument("--mock", action="store_true", help="Use the mock LLM regardless of environment.")
    p = sub.add_parser("coverage", help="Tool coverage per configuration (no LLM).")
    p.add_argument("--split", default="all", choices=["all", "dev", "test"])
    p = sub.add_parser("evaluate", help="Run the benchmark.")
    p.add_argument("--split", default="test", choices=["all", "dev", "test"])
    p.add_argument("--repeats", type=int, default=1)
    p.add_argument("--configs", nargs="+", default=sorted(CONFIGS), choices=sorted(CONFIGS))
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--label", default="")
    p.add_argument("--mock", action="store_true", help="Use the mock LLM regardless of environment.")
    args = parser.parse_args(argv)

    try:
        services = build_services(rebuild=getattr(args, "rebuild", False))
    except DatabaseBusyError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.command in ("bootstrap", "status"):
        print(json.dumps(services.status(), indent=2, default=str))
        return 0

    llm = make_llm("mock") if getattr(args, "mock", False) else None
    runtime = AgentRuntime(services, llm)
    if args.command == "ask":
        trace = runtime.run(args.question, args.config)
        for call in trace["tool_calls"]:
            flag = "ok" if call["ok"] else f"ERROR {call['error']['code']}"
            print(f"  [tool {call['index']}] {call['tool']}({json.dumps(call['arguments'], default=str)}) -> {flag}", file=sys.stderr)
        print(f"\n[{trace['llm']['provider']}:{trace['llm']['model']}] {trace['final_answer'] or trace['error']}")
        print(f"\n{json.dumps(trace['metrics'])}", file=sys.stderr)
        return 0

    evaluation = Evaluation(services, runtime)
    if args.command == "coverage":
        report = evaluation.coverage(split=None if args.split == "all" else args.split)
        print(f"{'config':<8}{'tools':>6}{'task cov':>10}{'objects':>9}{'relations':>11}{'operations':>12}{'direct':>8}{'multi':>7}")
        for key, c in report["configs"].items():
            print(f"{key:<8}{c['tool_count']:>6}{c['task_coverage_rate']:>10.1%}{c['object_access_coverage']:>9.1%}"
                  f"{c['relationship_access_coverage']:>11.1%}{c['operation_coverage']:>12.1%}{c['direct_tasks']:>8}{c['multi_call_tasks']:>7}")
        return 0

    output = evaluation.run(configs=args.configs, split=None if args.split == "all" else args.split, repeats=args.repeats,
                            limit=args.limit, concurrency=args.concurrency, label=args.label,
                            progress=lambda done, total: print(f"\r{done}/{total} runs", end="", file=sys.stderr))
    print(f"\n{output['notice']}\nLLM: {output['llm']['provider']}:{output['llm']['model']}  run id: {output['run_id']}")
    print(f"{'config':<8}{'coverage':>10}{'tool use ok':>13}{'success':>9}{'score/2':>9}{'halluc.':>9}{'calls':>7}{'latency':>10}")
    for key, s in output["summary"]["by_config"].items():
        use = s["tool_calling"]["correct_tool_use_rate"]
        e = s["end_to_end"]
        print(f"{key:<8}{s['coverage']['task_coverage_rate']:>10.1%}{(f'{use:.1%}' if use is not None else 'n/a'):>13}"
              f"{e['success_rate']:>9.1%}{e['mean_score_out_of_2']:>9.2f}{e['hallucination_rate']:>9.1%}"
              f"{e['avg_tool_calls']:>7.1f}{e['avg_latency_ms'] / 1000:>9.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
