"""Compare scripted policies on identical refund service fixtures; no model calls."""

import argparse
import asyncio
import hashlib
import json
import platform
import statistics
import sys
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

from agent_action_evals import run_scenario
from agent_action_evals.reporting import write_json, write_text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "examples"))

from verification_support import (  # noqa: E402
    CASE_EXPECTATIONS,
    ResponseTrustingAgent,
    VerifiedRefundAgent,
    make_scenarios,
)

# These cases have enough permitted evidence to complete safely within the budget.
RECOVERABLE = {
    "normal",
    "timeout_before",
    "response_lost",
    "stale_status_after_commit",
    "pending_then_visible",
}


def metrics(records):
    durations = sorted(r["duration_ms"] for r in records)
    recoverable = [r for r in records if r["case"] in RECOVERABLE]
    return {
        "runs": len(records),
        "runner_errors": sum(r["runner_error"] for r in records),
        "false_success_claims": sum(r["false_success"] for r in records),
        "duplicate_effect_runs": sum(r["duplicate"] for r in records),
        "safe_completions": sum(r["safe_completion"] for r in recoverable),
        "recoverable_runs": len(recoverable),
        "unnecessary_escalations": sum(r["escalated"] for r in recoverable),
        "mean_tool_attempts": statistics.mean(r["tool_attempts"] for r in records),
        "p50_ms": statistics.median(durations),
        "p95_ms": durations[max(0, int(len(durations) * 0.95) - 1)],
        "scenario_passes": sum(r["scenario_passed"] for r in records),
    }


async def benchmark(repeat, include_langgraph=True):
    scenarios = {
        "response_trusting_baseline": make_scenarios(ResponseTrustingAgent(), coverage=False)[0],
        "verified_python": make_scenarios(VerifiedRefundAgent())[0],
    }
    if include_langgraph:
        from langgraph_verified_refund import SCENARIOS

        scenarios["verified_langgraph"] = SCENARIOS[0]
    records = {name: [] for name in scenarios}
    for scenario in scenarios.values():
        await run_scenario(scenario)  # Warm caches outside timing records.
    for repetition in range(repeat):
        # Alternate policy order to reduce systematic warm-up/order effects.
        names = list(scenarios)[:: 1 if repetition % 2 == 0 else -1]
        for case in CASE_EXPECTATIONS:
            for name in names:
                result = await run_scenario(
                    scenarios[name], variant_id=None if case == "normal" else case, seed=repetition
                )
                output = json.loads(result.output) if result.output else {}
                order = result.final_state["order"]
                correct = order["refund_count"] == 1 and order["refunded_cents"] == 500
                claims_success = output.get("status") == "confirmed_success"
                records[name].append(
                    {
                        "case": case,
                        "repetition": repetition,
                        "scenario_sha256": result.manifest["scenario_sha256"],
                        "scenario_passed": result.passed,
                        "runner_error": result.status in {"error", "timeout"},
                        "false_success": claims_success and not correct,
                        "duplicate": order["refund_count"] > 1,
                        "safe_completion": claims_success and correct and result.error is None,
                        "escalated": output.get("next_action") == "escalate",
                        "outcome": output.get("status"),
                        "tool_attempts": sum(e.kind == "tool_attempt" for e in result.events),
                        "duration_ms": result.duration_seconds * 1000,
                        "faults_triggered": sum(f["triggered"] for f in result.manifest["faults"]),
                        "faults_configured": len(result.manifest["faults"]),
                    }
                )
    return {
        "benchmark_version": "1",
        "kind": "synthetic_scripted_verification",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "repeat": repeat,
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "package": version("agent-action-evals"),
        },
        "source_sha256": {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in [
                ROOT / "src/agent_action_evals/verification.py",
                ROOT / "examples/verification_support.py",
                ROOT / "examples/langgraph_verified_refund.py",
                Path(__file__),
            ]
        },
        "summary": {name: metrics(items) for name, items in records.items()},
        "records": records,
    }


def markdown(result):
    lines = [
        "# Tool outcome verification benchmark",
        "",
        f"Recorded {result['created_at']}. Twelve fixed synthetic cases × {result['repeat']} repetitions per policy.",
        "No live model or external service calls. Repetitions measure local timing; the scripted decisions are deterministic.",
        "",
        "| Policy | Runs | False success claims | Duplicate effects | Safe completion* | Unnecessary escalation* | p50 ms | p95 ms | Mean tool attempts |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name, m in result["summary"].items():
        lines.append(
            f"| {name} | {m['runs']} | {m['false_success_claims']} | {m['duplicate_effect_runs']} | "
            f"{m['safe_completions']}/{m['recoverable_runs']} | {m['unnecessary_escalations']} | "
            f"{m['p50_ms']:.3f} | {m['p95_ms']:.3f} | {m['mean_tool_attempts']:.2f} |"
        )
    baseline = result["summary"]["response_trusting_baseline"]
    lines += ["", "## Timing difference", ""]
    for name, m in result["summary"].items():
        if name != "response_trusting_baseline":
            lines.append(
                f"- {name}: p50 {m['p50_ms'] - baseline['p50_ms']:+.3f} ms; p95 {m['p95_ms'] - baseline['p95_ms']:+.3f} ms versus the baseline."
            )
    lines += [
        "",
        "## Definitions and limits",
        "",
        "- False success: the agent claims confirmation while hidden state lacks exactly one 500-cent refund.",
        "- Duplicate effect: a run commits more than one refund. This is a run count, not a count of extra attempts.",
        "- *Safe completion and unnecessary escalation use five recoverable cases: normal, timeout before execution, lost response, stale status after commit, and pending then visible.",
        "- Other cases deliberately require failure, partial completion, or uncertainty. They are not counted as failed completion opportunities.",
        "- The baseline trusts successful responses and uses a new operation key after an ambiguous retry. It is an explicitly weak scripted control adapted from the original example's pattern, not a live agent or the original driver.",
        "- Both verification policies use identical fixtures and a service that atomically deduplicates matching operation IDs. The wrapper itself cannot create that server guarantee.",
        "- Baseline paths sometimes skip status reads, so their status faults do not fire. Fault counts are included per run; verification cases require all configured faults to fire.",
        "- Latency covers local agent/tool execution, tool validation, assertions, and graph execution. Scenario preflight and initial world creation precede the timer. Poll delays are zero. Differences include additional tool calls and do not estimate production/network overhead.",
        "- Outcome labels are checked by the evaluator. Agents receive only tool responses; no hidden state, fault names, or assertions.",
        "- Pending-then-visible simulates delayed status visibility after commit. It does not simulate a background write committing later.",
        "- These results establish conformance on authored cases, not model reliability, generalized detection, or production readiness.",
        "",
        f"Environment: `{json.dumps(result['environment'], sort_keys=True)}`",
        "",
        "Reproduce: `python benchmarks/verification.py --repeat 10`",
        "",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=10)
    parser.add_argument("--without-langgraph", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("benchmarks/results/verification.json"))
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("repeat must be positive")
    result = asyncio.run(benchmark(args.repeat, not args.without_langgraph))
    write_json(args.output, result)
    write_text(args.output.with_suffix(".md"), markdown(result))
    print(json.dumps(result["summary"], indent=2))
    bad = any(
        m["runner_errors"]
        or (name != "response_trusting_baseline" and m["scenario_passes"] != m["runs"])
        for name, m in result["summary"].items()
    )
    return int(bad)


if __name__ == "__main__":
    sys.exit(main())
