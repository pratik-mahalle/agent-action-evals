from __future__ import annotations

import asyncio
import math
import platform
import time
import uuid
from dataclasses import dataclass
from importlib.metadata import version
from typing import Any

from .core import AgentResult, Context, Event, Finding, Scenario, ToolPort, ToolPrecondition
from .state import World, canonical
from .validation import digest, prepare_case


@dataclass(frozen=True)
class RunResult:
    scenario_id: str
    case_id: str
    run_id: str
    passed: bool
    output: str | None
    error: str | None
    findings: tuple[Finding, ...]
    events: tuple[Event, ...]
    final_state: dict[str, Any]
    bound_tools: tuple[str, ...]
    status: str
    duration_seconds: float
    manifest: dict[str, Any]


async def run_scenario(
    scenario: Scenario,
    *,
    variant_id: str | None = None,
    timeout_seconds: float = 60.0,
    seed: int = 0,
) -> RunResult:
    """Run one case. In-process drivers are trusted; DockerDriver enforces agent isolation."""
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be finite and positive")
    initial, assertions, faults, scenario_hash = prepare_case(scenario, variant_id)
    case_id = scenario.id + (f"/{variant_id}" if variant_id else "")
    context = Context(uuid.uuid4().hex, case_id, seed)
    world = World(initial)
    tools = ToolPort(
        world,
        scenario.handlers,
        faults,
        scenario.max_tool_calls,
        scenario.specifications,
        tuple(a for a in assertions if isinstance(a, ToolPrecondition)),
    )
    output, error, status = None, None, "pass"
    start = time.perf_counter()
    response_metadata = {}
    try:
        try:
            response = await asyncio.wait_for(
                scenario.driver.run(scenario.user_input, tools.client(), context), timeout_seconds
            )
            if not isinstance(response, AgentResult) or not isinstance(response.text, str):
                raise TypeError("Driver must return AgentResult with string text")
            canonical(response.metadata)
            output = response.text
            response_metadata = dict(response.metadata)
            tools.record("agent_output", text=output)
        except TimeoutError:
            error, status = "TimeoutError: run deadline exceeded", "timeout"
            tools.record("agent_error", error_type="TimeoutError")
        except Exception as exc:
            error, status = f"{type(exc).__name__}: {exc}", "error"
            tools.record("agent_error", error_type=type(exc).__name__)
        tools.seal()
        findings = list(tools.violations)
        try:
            for assertion in assertions:
                finding = assertion.evaluate(world, tools.events)
                if finding is not None:
                    findings.append(finding)
        except Exception as exc:
            error, status = f"EvaluatorError: {type(exc).__name__}: {exc}", "error"
        if findings and status == "pass":
            status = "fail"
        manifest = {
            "schema_version": "1.0",
            "package_version": version("agent-action-evals"),
            "python": platform.python_version(),
            "scenario_sha256": scenario_hash,
            "seed": seed,
            "timeout_seconds": timeout_seconds,
            "config_sha256": digest(scenario.metadata),
            "agent_config": dict(scenario.metadata),
            "agent_metadata": response_metadata,
            "isolation": getattr(scenario.driver, "isolation", "trusted_in_process"),
        }
        return RunResult(
            scenario.id,
            case_id,
            context.run_id,
            status == "pass",
            output,
            error,
            tuple(findings),
            tuple(tools.events),
            world.snapshot(),
            tuple(sorted(scenario.handlers)),
            status,
            time.perf_counter() - start,
            manifest,
        )
    finally:
        tools.seal()
        world.close()
