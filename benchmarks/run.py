"""Reproducible local evaluator conformance and no-model overhead benchmark."""

import argparse
import asyncio
import json
import platform
import statistics
import sys
import time
import tracemalloc
from importlib.metadata import version
from pathlib import Path

from corpus import FAMILIES, Policy, act, corpus, read

from agent_action_evals import Scenario, StateEquals, run_scenario
from agent_action_evals.reporting import write_json


def percentile(values, fraction):
    values = sorted(values)
    return values[min(len(values) - 1, int((len(values) - 1) * fraction))]


async def correctness():
    tp = tn = fp = fn = errors = 0
    records = []
    localized = expected_locations = 0
    for scenario, variant, unsafe in corpus():
        result = await run_scenario(scenario, variant_id=variant)
        predicted = result.status == "fail"
        errors += result.status in {"error", "timeout"}
        tp += int(unsafe and predicted)
        fn += int(unsafe and not predicted)
        fp += int(not unsafe and predicted)
        tn += int(not unsafe and not predicted and result.status == "pass")
        location_ok = None
        if unsafe:
            # Scripts define the first offending act: the second act on response loss,
            # otherwise the first. This label is independent of the assertion engine.
            acts = [e for e in result.events if e.kind == "tool_executed" and e.tool == "act"]
            index = 1 if scenario.metadata["family"] == "lost_response" else 0
            expected_locations += 1
            expected = acts[index].seq if len(acts) > index else None
            location_ok = expected is not None and any(
                f.event_seq == expected for f in result.findings
            )
            localized += int(location_ok)
        records.append(
            {
                "case": result.case_id,
                "expected_unsafe": unsafe,
                "status": result.status,
                "first_effect_localized": location_ok,
                "scenario_sha256": result.manifest["scenario_sha256"],
            }
        )
    return {
        "runs": len(records),
        "templates": len(FAMILIES),
        "paired_tasks": 40,
        "true_positive": tp,
        "true_negative": tn,
        "false_positive": fp,
        "false_negative": fn,
        "runner_errors": errors,
        "recall": tp / (tp + fn),
        "false_positive_rate": fp / (fp + tn),
        "first_effect_localized": localized,
        "unsafe_cases": expected_locations,
        "records": records,
    }


async def overhead(count, padding):
    state = {
        "effects": 0,
        "verified": True,
        "owner": "a",
        "caller": "a",
        "approved": True,
        "matches": 1,
        "amount": 10,
        "limit": 20,
        "padding": "x" * padding,
    }
    scenario = Scenario(
        "overhead",
        state,
        "Act",
        Policy("identity", False),
        {"read": read, "act": act},
        (StateEquals("effects", 1),),
    )
    for _ in range(10):
        await run_scenario(scenario)
    durations = []
    started = time.perf_counter()
    for _ in range(count):
        one = time.perf_counter()
        result = await run_scenario(scenario)
        if not result.passed:
            raise RuntimeError("Performance scenario failed")
        durations.append((time.perf_counter() - one) * 1000)
    elapsed = time.perf_counter() - started
    # Memory measured separately so tracing does not distort the latency measurement.
    tracemalloc.start()
    for _ in range(20):
        await run_scenario(scenario)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "runs": count,
        "fixture_bytes": len(json.dumps(state)),
        "elapsed_seconds": elapsed,
        "runs_per_second": count / elapsed,
        "p50_ms": statistics.median(durations),
        "p95_ms": percentile(durations, 0.95),
        "peak_python_bytes_20_runs": peak,
    }


async def main(args):
    result = {
        "benchmark_version": "1",
        "kind": "synthetic_no_model",
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "package": version("agent-action-evals"),
        },
        "correctness": await correctness(),
        "performance": [],
    }
    print(f"Correctness: {result['correctness']['runs']} labeled runs", flush=True)
    for size in args.sizes:
        for padding in (1024, 65536):
            record = await overhead(size, padding)
            result["performance"].append(record)
            print(
                f"{size} runs / {padding} padding bytes: p95 {record['p95_ms']:.2f} ms", flush=True
            )
    write_json(args.output, result)
    c = result["correctness"]
    lines = [
        "# Local benchmark results",
        "",
        "Synthetic evaluator conformance; no model API calls.",
        "",
        f"Environment: {result['environment']}",
        "",
        f"Eight scripted behavior templates × five fixtures = 40 paired tasks; {c['runs']} labeled runs.",
        f"Detected {c['true_positive']}/{c['unsafe_cases']} seeded unsafe cases; "
        f"{c['false_positive']} false alarms; {c['runner_errors']} runner errors.",
        f"Localized the first wrong physical effect in {c['first_effect_localized']}/{c['unsafe_cases']} unsafe cases.",
        "",
        "| Runs | Fixture bytes | p50 ms | p95 ms | Runs/sec | Peak Python bytes (20 runs) |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for p in result["performance"]:
        lines.append(
            f"| {p['runs']} | {p['fixture_bytes']} | {p['p50_ms']:.3f} | {p['p95_ms']:.3f} | "
            f"{p['runs_per_second']:.1f} | {p['peak_python_bytes_20_runs']} |"
        )
    lines += [
        "",
        "Timing includes validation, hashing, fixture creation, two tool calls, assertions, and cleanup.",
        "Memory is Python allocation peak during a separate 20-run sample, not process RSS.",
        "These parameterized scripts test known failure mechanics. They do not establish real-agent",
        "reliability, generalization to new failures, sandbox security, or superiority to another framework.",
    ]
    args.output.with_suffix(".md").write_text("\n".join(lines) + "\n")
    return int(c["false_negative"] or c["false_positive"] or c["runner_errors"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", type=int, nargs="+", default=[100, 1000, 10000])
    parser.add_argument("--output", type=Path, default=Path("benchmarks/results/local.json"))
    args = parser.parse_args()
    if any(n < 1 for n in args.sizes):
        parser.error("sizes must be positive")
    sys.exit(asyncio.run(main(args)))
