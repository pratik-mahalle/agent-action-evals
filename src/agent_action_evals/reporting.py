from __future__ import annotations

import json
import math
import os
import shlex
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

from .runner import RunResult

SCHEMA_VERSION = "1.0"


def serialize(result: RunResult, include_payloads: bool = False):
    manifest = dict(result.manifest)
    if not include_payloads:
        manifest.pop("agent_config", None)
        metadata = manifest.pop("agent_metadata", {})
        manifest["agent_usage"] = {
            k: v
            for k, v in metadata.get("usage", {}).items()
            if k in {"input_tokens", "output_tokens", "total_tokens"} and type(v) is int
        }
        if isinstance(metadata.get("image_id"), str) and metadata["image_id"].startswith("sha256:"):
            manifest["image_id"] = metadata["image_id"]
    return {
        "schema_version": SCHEMA_VERSION,
        "scenario_id": result.scenario_id,
        "case_id": result.case_id,
        "run_id": result.run_id,
        "passed": result.passed,
        "status": result.status,
        "duration_seconds": result.duration_seconds,
        "manifest": manifest,
        "output": result.output if include_payloads else None,
        "error": result.error if include_payloads else ("run_error" if result.error else None),
        "findings": [
            {
                "rule": f.rule,
                "event_seq": f.event_seq,
                "attribution": f.attribution,
                "expected": f.expected if include_payloads else "<redacted>",
                "actual": f.actual if include_payloads else "<redacted>",
            }
            for f in result.findings
        ],
        "events": [
            {
                "seq": e.seq,
                "kind": e.kind,
                "tool": e.tool if e.tool in result.bound_tools else None,
                "details": e.details
                if include_payloads
                else {
                    k: v
                    for k, v in e.details.items()
                    if k in {"call_number", "status", "fault_kind", "error_type"}
                },
            }
            for e in result.events
        ],
        "final_state": result.final_state if include_payloads else "<redacted>",
        "bound_tools": result.bound_tools,
    }


def wilson(passed: int, total: int):
    if not total:
        return None
    z = 1.959963984540054
    p = passed / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return [max(0, centre - margin), min(1, centre + margin)]


def summary(results):
    counts = Counter(r.status for r in results)
    cases = {}
    for result in results:
        bucket = cases.setdefault(result.case_id, {"runs": 0, "passed": 0})
        bucket["runs"] += 1
        bucket["passed"] += int(result.passed)
    for bucket in cases.values():
        bucket["pass_rate"] = bucket["passed"] / bucket["runs"]
        bucket["wilson_95_interval"] = wilson(bucket["passed"], bucket["runs"])
    return {"runs": len(results), "statuses": dict(counts), "cases": cases}


def write_json(path: Path, value):
    write_text(path, json.dumps(value, indent=2, allow_nan=False) + "\n")


def write_text(path: Path, value: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(value)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def rerun_command(result: RunResult, scenarios: Path, include_payloads: bool = False) -> str:
    argv = [
        "python",
        "-m",
        "agent_action_evals",
        str(scenarios.resolve()),
        "--case",
        result.case_id,
        "--seed",
        str(result.manifest["seed"]),
        "--timeout",
        str(result.manifest["timeout_seconds"]),
        "--explain",
    ]
    if include_payloads:
        argv.append("--include-payloads")
    return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)


def _value(value) -> str:
    # Escape control characters so payloads cannot inject fake terminal events.
    text = json.dumps(value, ensure_ascii=True, allow_nan=False, default=str)
    return text if len(text) <= 600 else text[:600] + "... [truncated; see JSON report]"


def explain(result: RunResult, scenarios: Path, include_payloads: bool = False) -> str:
    """Readable event evidence; retain attribution uncertainty and redact by default."""
    data = serialize(result, include_payloads)
    lines = [f"{result.status.upper()} {result.case_id} [{result.run_id[:8]}]"]
    lines.append(f"Scenario SHA256: {result.manifest['scenario_sha256']}")
    for fault in result.manifest.get("faults", []):
        coverage = "injected" if fault["triggered"] else "NOT REACHED"
        lines.append(
            f"Fault: {fault['tool']} call {fault['on_call']} / {fault['kind']} — {coverage}"
        )
    if any(not f["triggered"] for f in result.manifest.get("faults", [])):
        lines.append("An unexercised fault provides no evidence of recovery from that fault.")
    if data["error"]:
        lines.append(f"Run error: {_value(data['error'])}")
    lines.append("Timeline:")
    marked = {finding.event_seq for finding in result.findings if finding.event_seq is not None}
    labels = {
        "tool_attempt": "attempted",
        "tool_executed": "executed",
        "tool_observation": "agent received result",
        "tool_rejected": "call rejected",
        "precondition_failed": "precondition violated before execution",
        "tool_error": "handler raised",
        "agent_output": "agent returned",
        "agent_error": "agent raised",
    }
    fault_labels = {
        "timeout_before": "timeout before execution; handler did not run",
        "response_lost_after_commit": "response lost after execution; agent received an error",
        "stale_read": "stale result substituted; handler did not run",
    }
    for raw, event in zip(result.events, data["events"]):
        details = event["details"]
        label = labels.get(event["kind"], event["kind"])
        if event["kind"] == "tool_fault":
            label = fault_labels.get(details.get("fault_kind"), "fault injected")
        if event["kind"] == "tool_attempt":
            label += f" (call {details.get('call_number')})"
        if event["kind"] == "tool_executed":
            count = len(raw.details.get("changes", {}))
            label += f"; status={details.get('status')}; {count} state field(s) changed"
        if details.get("error_type"):
            label += f" ({_value(details['error_type'])})"
        marker = " !" if event["seq"] in marked else ""
        lines.append(f"  {event['seq']:>3}{marker} {event['tool'] or 'agent'}: {label}")
        if include_payloads:
            for key in ("arguments", "changes", "result", "actual", "text", "reason"):
                if key in details:
                    lines.append(f"        {key}: {_value(details[key])}")
    lines.append("Findings:")
    if not data["findings"]:
        lines.append("  No assertion violations recorded.")
    for finding in data["findings"]:
        where = (
            f"event {finding['event_seq']}"
            if finding["event_seq"] is not None
            else "no event attributed"
        )
        lines.append(f"  {_value(finding['rule'])}: {where}; {finding['attribution']}")
        if include_payloads:
            lines.append(f"    expected: {_value(finding['expected'])}")
            lines.append(f"    actual:   {_value(finding['actual'])}")
    if not include_payloads:
        lines.append("Payloads redacted. Use --include-payloads for scrubbed local debugging.")
    lines.extend(["Rerun:", "  " + rerun_command(result, scenarios, include_payloads)])
    lines.append("Reruns reuse the case and seed; live model output may vary.")
    return "\n".join(lines)


def text_report(results, scenarios: Path, include_payloads: bool = False) -> str:
    counts = summary(results)
    faults = [fault for result in results for fault in result.manifest.get("faults", [])]
    lines = [
        "Agent Action Evals",
        f"{sum(r.passed for r in results)}/{len(results)} runs passed",
        "Statuses: " + ", ".join(f"{key}={value}" for key, value in counts["statuses"].items()),
        f"Fault coverage: {sum(f['triggered'] for f in faults)}/{len(faults)} injections exercised",
        "",
    ]
    lines.extend(
        f"{result.status.upper()} {result.case_id} [{result.run_id[:8]}] "
        f"seed={result.manifest['seed']}"
        for result in results
    )
    for result in results:
        if not result.passed:
            lines.extend(["", explain(result, scenarios, include_payloads)])
    return "\n".join(lines) + "\n"


def write_junit(path: Path, results, include_payloads=False):
    suite = ET.Element(
        "testsuite",
        name="agent-action-evals",
        tests=str(len(results)),
        failures=str(sum(r.status == "fail" for r in results)),
        errors=str(sum(r.status in {"error", "timeout"} for r in results)),
    )
    for result in results:
        case = ET.SubElement(
            suite,
            "testcase",
            name=f"{result.case_id}:{result.run_id}",
            classname=result.scenario_id,
            time=str(result.duration_seconds),
        )
        if not result.passed:
            message = "; ".join(f.rule for f in result.findings) or result.status
            if include_payloads:
                message += f" {result.error or ''} {result.findings!r}"
            tag = "failure" if result.status == "fail" else "error"
            ET.SubElement(case, tag, message=message).text = message
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(suite).write(path, encoding="unicode", xml_declaration=True)
