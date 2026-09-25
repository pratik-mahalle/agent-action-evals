"""Compare AAE with the pinned Failproof SDK worker on three paired cases."""

import argparse
import asyncio
import copy
import hashlib
import json
import platform
import socket
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

import aae
from cases import CASES
from failproof_simulator import Simulator

from agent_action_evals.reporting import write_json

PIN = "b8cce3b221f94b74b7283a6df63ffb603712776a"
HERE = Path(__file__).resolve().parent


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_failproof(root):
    head = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    if head != PIN:
        raise ValueError(f"Expected Failproof commit {PIN}; found {head}")
    dirty = subprocess.check_output(
        [
            "git",
            "-C",
            str(root),
            "status",
            "--porcelain",
            "--untracked-files=no",
            "--",
            "sdk/python",
        ],
        text=True,
    )
    if dirty:
        raise ValueError("Failproof SDK has local modifications")
    sys.path.insert(0, str(root / "sdk/python"))
    import failproof_local
    import failproofai_sdk

    loaded = Path(failproofai_sdk.__file__).resolve()
    if not loaded.is_relative_to(root / "sdk/python"):
        raise ValueError("A different Failproof SDK was already loaded")
    return failproof_local, failproofai_sdk.__version__


def normalize_aae(result):
    observations = []
    for event in result.events:
        if event.kind == "tool_observation":
            observations.append([event.tool, event.details["result"], None])
        elif (
            event.kind == "tool_fault"
            and event.details["fault_kind"] == "response_lost_after_commit"
        ):
            observations.append([event.tool, None, "response_lost"])
    return {
        "calls": [e.tool for e in result.events if e.kind == "tool_attempt"],
        "observations": observations,
        "final_state": result.final_state,
    }


def normalize_failproof(events):
    return {
        "calls": [e["payload"]["tool_name"] for e in events if e["type"] == "tool_use"],
        "observations": [
            [e["payload"]["tool_name"], e["payload"]["output"], e["payload"]["error"]]
            for e in events
            if e["type"] == "tool_result"
        ],
        "final_state": events[-1]["payload"]["final_state"],
    }


def measures(records, backend):
    def count(unsafe, status):
        return sum(
            r["expected_unsafe"] == unsafe and r[backend]["status"] == status for r in records
        )

    unsafe = [r for r in records if r["expected_unsafe"]]
    return {
        "true_positive": count(True, "fail"),
        "true_negative": count(False, "pass"),
        "false_positive": count(False, "fail"),
        "false_negative": count(True, "pass"),
        "errors": sum(r[backend]["status"] in {"error", "timeout"} for r in records),
        "first_effect_localized": sum(r[backend]["first_effect_localized"] for r in unsafe),
    }


async def compare(local, sdk_version, root):
    records = []
    for case in CASES:
        result = await aae.evaluate(case)
        events = await Simulator(case).run()
        sdk_result = await local.evaluate(case.id, events)
        same_behavior = normalize_aae(result) == normalize_failproof(events)
        aae_refunds = [
            e for e in result.events if e.kind == "tool_executed" and e.tool == "issue_refund"
        ]
        fp_refunds = [
            e
            for e in events
            if e["type"] == "tool_result" and e["payload"]["tool_name"] == "issue_refund"
        ]
        bad_index = 1 if case.family == "lost_response" else 0
        aae_localized = fp_localized = None
        explanation = json.loads(sdk_result.get("reasoning", "{}"))
        if case.expected_unsafe:
            aae_localized = len(aae_refunds) > bad_index and any(
                f.event_seq == aae_refunds[bad_index].seq for f in result.findings
            )
            fp_localized = len(fp_refunds) > bad_index and explanation.get(
                "violating_event_ids", []
            )[:1] == [fp_refunds[bad_index]["id"]]
        records.append(
            {
                "case": case.id,
                "expected_unsafe": case.expected_unsafe,
                "behavior_matches": same_behavior,
                "aae": {
                    "status": result.status,
                    "error": result.error,
                    "first_effect_localized": aae_localized,
                    "final_state": result.final_state,
                    "findings": [asdict(f) for f in result.findings],
                    "events": [asdict(e) for e in result.events],
                },
                "failproof": {
                    **sdk_result,
                    "first_effect_localized": fp_localized,
                    "events": events,
                },
            }
        )
        print(
            f"{case.id}: AAE={result.status}, Failproof SDK={sdk_result['status']}, parity={same_behavior}"
        )

    # A stripped trace must not silently get a passing state-based verdict.
    stripped = copy.deepcopy(records[0]["failproof"]["events"])
    stripped[-1]["payload"].pop("final_state")
    missing = await local.evaluate("missing_state_evidence", stripped)
    source_files = {
        p.name: {
            "sha256": sha256(p),
            "nonblank_noncomment_lines": sum(
                bool(line.strip()) and not line.lstrip().startswith("#")
                for line in p.read_text().splitlines()
            ),
        }
        for p in sorted(HERE.glob("*.py"))
    }
    sdk_files = (root / "sdk/python/failproofai_sdk").rglob("*.py")
    sdk_hash = hashlib.sha256(
        "\n".join(f"{p.relative_to(root)}:{sha256(p)}" for p in sorted(sdk_files)).encode()
    ).hexdigest()
    return {
        "comparison_version": "1",
        "kind": "offline_sdk_component_comparison",
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "failproof": {
            "repository": "https://github.com/FailproofAI/failproofai",
            "commit": PIN,
            "sdk_version": sdk_version,
            "sdk_sources_sha256": sdk_hash,
            "execution": "real WorkerRuntime; custom checks; in-memory server transport",
        },
        "summary": {
            "cases": len(records),
            "unsafe": 3,
            "safe": 9,
            "behavior_matches": sum(r["behavior_matches"] for r in records),
            "aae": measures(records, "aae"),
            "failproof": measures(records, "failproof"),
        },
        "missing_evidence_control": missing,
        "authored_files": source_files,
        "records": records,
        "limitations": [
            "Three synthetic failure families with scripted agents; no live model calls.",
            "Failproof Cloud ingestion, hosted execution, dashboards, and runtime policies were not exercised.",
            "Failproof received custom authoritative state telemetry from an independently executed simulator.",
            "AAE supplied native fixture/fault handling; Failproof needed benchmark-authored simulation plumbing.",
            "Source-line counts describe this implementation and exclude existing library code; not a controlled developer-effort study.",
            "Custom checks can change the outcome; these are not Failproof built-in checks or proof of general superiority.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--failproof-root", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, default=Path("benchmarks/results/failproof-comparison.json")
    )
    args = parser.parse_args()
    root = args.failproof_root.resolve(strict=True)
    local, sdk_version = load_failproof(root)
    # The benchmark exercises an offline path; accidental network use is a failure.
    original = socket.socket.connect
    original_ex = socket.socket.connect_ex

    def deny_network(*args, **kwargs):
        raise RuntimeError("Network access is disabled for the offline comparison")

    try:
        # Create asyncio's loop before the guard (Windows may use a loopback socketpair).
        with asyncio.Runner() as runner:
            runner.get_loop()
            socket.socket.connect = deny_network
            socket.socket.connect_ex = deny_network
            started = time.perf_counter()
            report = runner.run(compare(local, sdk_version, root))
            report["total_wall_seconds"] = time.perf_counter() - started
    finally:
        socket.socket.connect = original
        socket.socket.connect_ex = original_ex
    write_json(args.output, report)
    print(json.dumps(report["summary"], indent=2))
    expected = {
        "true_positive": 3,
        "true_negative": 9,
        "false_positive": 0,
        "false_negative": 0,
        "errors": 0,
        "first_effect_localized": 3,
    }
    return int(
        report["summary"]["aae"] != expected
        or report["summary"]["failproof"] != expected
        or report["summary"]["behavior_matches"] != 12
        or report["missing_evidence_control"]["status"] != "error"
    )


if __name__ == "__main__":
    sys.exit(main())
