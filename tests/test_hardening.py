import asyncio
import json
from dataclasses import replace

import pytest

from agent_action_evals import (
    AgentResult,
    Scenario,
    StateEquals,
    ToolCallCount,
    ToolClient,
    ToolPrecondition,
    ToolSpec,
    run_scenario,
)
from agent_action_evals.reporting import serialize, write_junit
from agent_action_evals.validation import prepare_case


class Driver:
    def __init__(self, operation):
        self.operation = operation

    async def run(self, user_input, tools, context):
        await self.operation(tools)
        return AgentResult("payload-secret", {"secret": "metadata-secret"})


def case(operation, handlers=None, **kwargs):
    return Scenario(
        "test",
        {"n": 0, "approved": False},
        "go",
        Driver(operation),
        handlers or {"act": lambda w, a: w.set("n", w.get("n") + 1)},
        kwargs.pop("assertions", (StateEquals("n", 0),)),
        **kwargs,
    )


def run(scenario, **kwargs):
    return asyncio.run(run_scenario(scenario, **kwargs))


def test_caught_unknown_call_cannot_pass():
    async def operation(tools):
        try:
            await tools.call("not_registered")
        except Exception:
            pass

    result = run(case(operation))
    assert result.status == "fail"
    assert result.findings[0].rule == "unknown tool"


def test_caught_call_limit_cannot_pass():
    async def operation(tools):
        for _ in range(2):
            try:
                await tools.call("read")
            except Exception:
                pass

    result = run(case(operation, {"read": lambda w, a: 0}, max_tool_calls=1))
    assert result.status == "fail"


def test_invalid_arguments_never_execute():
    async def operation(tools):
        try:
            await tools.call("act", amount="wrong")
        except Exception:
            pass

    spec = ToolSpec(
        "act",
        {
            "type": "object",
            "properties": {"amount": {"type": "integer"}},
            "required": ["amount"],
            "additionalProperties": False,
        },
    )
    result = run(case(operation, specifications={"act": spec}))
    assert result.final_state["n"] == 0
    assert result.status == "fail"


def test_partial_commit_survives_handler_error():
    def handler(world, args):
        world.set("n", 1)
        raise ValueError("secret-error")

    async def operation(tools):
        try:
            await tools.call("act")
        except ValueError:
            pass

    result = run(case(operation, {"act": handler}))
    assert result.status == "fail"
    assert result.final_state["n"] == 1
    assert any(e.kind == "tool_executed" and e.details["status"] == "error" for e in result.events)


def test_timeout_records_commit_and_closes_world():
    captured = []

    async def handler(world, args):
        captured.append(world)
        world.set("n", 1)
        await asyncio.sleep(60)

    async def operation(tools):
        await tools.call("act")

    result = run(case(operation, {"act": handler}), timeout_seconds=0.01)
    assert result.status == "timeout"
    assert result.final_state["n"] == 1
    assert any(e.details.get("status") == "cancelled" for e in result.events)
    with pytest.raises(RuntimeError, match="sealed"):
        captured[0].set("n", 2)


def test_parallel_calls_are_serialized_and_runs_are_independent():
    async def handler(world, args):
        before = world.get("n")
        await asyncio.sleep(0)
        world.set("n", before + 1)

    async def operation(tools):
        await asyncio.gather(*(tools.call("act") for _ in range(10)))

    scenario = case(operation, {"act": handler}, assertions=(StateEquals("n", 10),))

    async def many():
        return await asyncio.gather(*(run_scenario(scenario) for _ in range(10)))

    assert all(r.passed for r in asyncio.run(many()))
    assert scenario.initial_state["n"] == 0


def test_precondition_detects_violation_even_if_final_state_is_repaired():
    async def operation(tools):
        await tools.call("act")
        await tools.call("repair")

    scenario = case(
        operation,
        {"act": lambda w, a: w.set("n", 1), "repair": lambda w, a: w.set("n", 0)},
        assertions=(StateEquals("n", 0), ToolPrecondition("act", "approved", True)),
    )
    result = run(scenario)
    assert result.status == "fail"
    assert len(result.findings) == 1
    assert result.findings[0].attribution == "direct"


def test_read_only_handler_cannot_hide_mutation():
    async def operation(tools):
        await tools.call("act")

    result = run(
        case(
            operation,
            assertions=(StateEquals("n", 1),),
            specifications={"act": ToolSpec("read", {"type": "object"}, read_only=True)},
        )
    )
    assert result.status == "fail"
    assert any(f.rule == "read-only tool changed state" for f in result.findings)


@pytest.mark.parametrize(
    "assertions",
    [
        (),
        (ToolCallCount("act", -1),),
        (ToolCallCount("act", 0, "typo"),),
        (StateEquals("n", 0), StateEquals("n", 1)),
    ],
)
def test_preflight_rejects_invalid_assertions(assertions):
    async def operation(tools):
        raise AssertionError("Must not run")

    with pytest.raises(ValueError):
        run(case(operation, assertions=assertions))


def test_missing_is_not_equal_to_literal_missing_string():
    from agent_action_evals import World

    world = World({"x": 0})
    try:
        assert StateEquals("absent", "<missing>").evaluate(world, []) is not None
        assert StateEquals("x", False).evaluate(world, []) is not None
    finally:
        world.close()


def test_scenario_hash_is_stable_and_changes_with_configuration():
    async def operation(tools):
        pass

    scenario = case(operation)
    first, second = run(scenario), run(scenario)
    assert first.manifest["scenario_sha256"] == second.manifest["scenario_sha256"]
    assert (
        run(replace(scenario, metadata={"model": "different"})).manifest["scenario_sha256"]
        != first.manifest["scenario_sha256"]
    )


def test_reports_redact_payloads_and_classify_errors(tmp_path):
    async def operation(tools):
        await tools.call("act", secret="argument-secret")

    result = run(case(operation, {"act": lambda w, a: {"result": "result-secret"}}))
    encoded = json.dumps(serialize(result))
    for marker in ("payload-secret", "metadata-secret", "argument-secret", "result-secret"):
        assert marker not in encoded
    assert "payload-secret" in json.dumps(serialize(result, True))
    path = tmp_path / "junit.xml"
    write_junit(path, [result])
    assert "payload-secret" not in path.read_text()


def test_schema_cannot_fetch_remote_references():
    async def operation(tools):
        pass

    scenario = case(
        operation, specifications={"act": ToolSpec("act", {"$ref": "https://example.com"})}
    )
    with pytest.raises(ValueError, match="document-local"):
        prepare_case(scenario, None)


def test_agent_client_does_not_expose_world():
    assert not hasattr(ToolClient(lambda: None, {}), "world")
