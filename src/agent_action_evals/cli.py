from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
from pathlib import Path

from .core import Scenario
from .reporting import (
    SCHEMA_VERSION,
    explain,
    serialize,
    summary,
    text_report,
    write_json,
    write_junit,
    write_text,
)
from .runner import run_scenario
from .validation import prepare_case


def load_scenarios(path: Path) -> tuple[Scenario, ...]:
    path = path.resolve(strict=True)
    spec = importlib.util.spec_from_file_location("agent_action_evals_cases", path)
    if spec is None or spec.loader is None:
        raise ValueError("Cannot load scenario module")
    module = importlib.util.module_from_spec(spec)
    # Scenario files are trusted Python code. Local imports are supported.
    sys.path.insert(0, str(path.parent))
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    scenarios = tuple(getattr(module, "SCENARIOS", ()))
    if not scenarios or any(not isinstance(s, Scenario) for s in scenarios):
        raise ValueError("SCENARIOS must contain at least one Scenario")
    if len({s.id for s in scenarios}) != len(scenarios):
        raise ValueError("Duplicate scenario IDs")
    for scenario in scenarios:
        prepare_case(scenario, None)  # validates all variants before any agent is invoked
    return scenarios


async def _run(scenarios, args):
    results = []
    stream = None
    if args.jsonl:
        args.jsonl.parent.mkdir(parents=True, exist_ok=True)
        stream = args.jsonl.open("w", encoding="utf-8")
    try:
        for scenario in scenarios:
            for variant_id in (None, *(v.id for v in scenario.variants)):
                case_id = scenario.id + (f"/{variant_id}" if variant_id else "")
                if args.case and case_id != args.case:
                    continue
                for repeat in range(args.repeat):
                    result = await run_scenario(
                        scenario,
                        variant_id=variant_id,
                        timeout_seconds=args.timeout,
                        seed=args.seed + repeat,
                    )
                    results.append(result)
                    if args.explain and not result.passed:
                        print(explain(result, args.scenarios, args.include_payloads))
                    else:
                        print(f"{result.status.upper()} {result.case_id} [{result.run_id[:8]}]")
                        for finding in result.findings:
                            print(
                                f"  {finding.rule}; event {finding.event_seq}; {finding.attribution}"
                            )
                        if args.include_payloads and result.error:
                            print(f"  {result.error}")
                    if stream:
                        stream.write(json.dumps(serialize(result, args.include_payloads)) + "\n")
                        stream.flush()
    finally:
        if stream:
            stream.close()
    if not results:
        raise ValueError("No cases matched --case")
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run stateful agent action evaluations")
    parser.add_argument("scenarios", type=Path, help="Trusted Python file exporting SCENARIOS")
    parser.add_argument("--validate", action="store_true", help="Validate without invoking agents")
    parser.add_argument(
        "--list-cases", action="store_true", help="List cases without invoking agents"
    )
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--case", help="Select a case by its full ID")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--jsonl", type=Path, help="Checkpoint each completed run")
    parser.add_argument("--junit", type=Path)
    parser.add_argument(
        "--explain", action="store_true", help="Show evidence and reruns for failures"
    )
    parser.add_argument("--text", type=Path, help="Write a readable summary and failure timelines")
    parser.add_argument("--include-payloads", action="store_true")
    args = parser.parse_args(argv)
    if args.repeat < 1:
        parser.error("--repeat must be positive")
    try:
        scenarios = load_scenarios(args.scenarios)
        cases = [
            scenario.id + (f"/{variant_id}" if variant_id else "")
            for scenario in scenarios
            for variant_id in (None, *(v.id for v in scenario.variants))
        ]
        if args.case and args.case not in cases:
            raise ValueError("No cases matched --case")
        if args.list_cases:
            print("\n".join(case for case in cases if not args.case or case == args.case))
            return 0
        if args.validate:
            print(f"Validated {len(scenarios)} scenarios and all their variants")
            return 0
        results = asyncio.run(_run(scenarios, args))
        if args.json:
            write_json(
                args.json,
                {
                    "schema_version": SCHEMA_VERSION,
                    "summary": summary(results),
                    "runs": [serialize(r, args.include_payloads) for r in results],
                },
            )
        if args.junit:
            write_junit(args.junit, results, args.include_payloads)
        if args.text:
            write_text(args.text, text_report(results, args.scenarios, args.include_payloads))
    except (OSError, ValueError, TypeError, AttributeError, ImportError) as exc:
        parser.error(str(exc))
    print(f"{sum(r.passed for r in results)}/{len(results)} runs passed")
    if any(r.status in {"error", "timeout"} for r in results):
        return 2
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
