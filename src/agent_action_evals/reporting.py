from __future__ import annotations

import json
import math
import os
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
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


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
