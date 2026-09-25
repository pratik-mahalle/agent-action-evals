import asyncio
from dataclasses import replace

import pytest

from agent_action_evals import (
    AgentResult,
    Fault,
    FaultPlan,
    Scenario,
    StateEquals,
    ToolResponseLost,
    ToolSpec,
    ToolTimeout,
    run_scenario,
    with_fault_matrix,
)


class Reader:
    async def run(self, user_input, tools, context):
        for _ in range(2):
            try:
                await tools.call("read")
            except (ToolTimeout, ToolResponseLost):
                pass
        return AgentResult("done")


def base():
    return Scenario(
        "matrix",
        {"count": 0},
        "read",
        Reader(),
        {"read": lambda world, args: world.get("count")},
        (StateEquals("count", 0),),
        specifications={
            "read": ToolSpec("Read count", {"type": "object"}, {"type": "integer"}, True)
        },
    )


def test_matrix_injects_each_selected_kind_and_occurrence_with_stable_ids():
    original = base()
    plan = FaultPlan("read", on_calls=(1, 2))
    scenario = with_fault_matrix(original, plan)
    assert not original.variants
    assert scenario.variants == with_fault_matrix(original, plan).variants
    assert len(scenario.variants) == 4
    for variant in scenario.variants:
        result = asyncio.run(run_scenario(scenario, variant_id=variant.id))
        assert result.passed, result.findings
        injections = [event for event in result.events if event.kind == "tool_fault"]
        assert len(injections) == 1
        assert injections[0].details["call_number"] == variant.faults[0].on_call
        assert result.manifest["faults"][0]["triggered"] is True


def test_unreached_fault_fails_instead_of_claiming_recovery():
    scenario = with_fault_matrix(base(), FaultPlan("read", on_calls=(3,)))
    result = asyncio.run(run_scenario(scenario, variant_id=scenario.variants[0].id))
    assert result.status == "fail"
    assert result.error is None
    assert len(result.findings) == 1
    assert result.findings[0].actual == "not reached"
    assert result.findings[0].event_seq is None
    assert result.manifest["faults"][0]["triggered"] is False


def test_stale_value_and_overrides_are_independent_of_mutable_plan_input():
    overrides = {"count": 1}
    original = base()
    scenario = with_fault_matrix(
        original,
        FaultPlan(
            "read",
            ("stale_read",),
            assertions=(StateEquals("count", 1),),
            overrides=overrides,
            stale_value=0,
        ),
    )
    overrides["count"] = 99
    variant = scenario.variants[0]
    result = asyncio.run(run_scenario(scenario, variant_id=variant.id))
    assert result.passed
    observations = [e.details["result"] for e in result.events if e.kind == "tool_observation"]
    assert observations == [0, 1]
    assert original.initial_state == {"count": 0}


@pytest.mark.parametrize(
    "plan",
    [
        FaultPlan("unknown"),
        FaultPlan("read", ("invalid",)),
        FaultPlan("read", on_calls=(0,)),
        FaultPlan("read", on_calls=(51,)),
        FaultPlan("read", on_calls=(1, 1)),
        FaultPlan("read", kinds=()),
        FaultPlan("read", ("stale_read",)),
        FaultPlan("read", ("stale_read",), stale_value="wrong type"),
        FaultPlan("read", assertions=()),
        FaultPlan("read", overrides={"absent": 2}),
    ],
)
def test_invalid_matrix_is_rejected_before_driver_invocation(plan):
    with pytest.raises(ValueError):
        with_fault_matrix(base(), plan)


def test_size_limit_duplicate_cases_and_existing_base_faults_are_rejected():
    with pytest.raises(ValueError, match="limit"):
        with_fault_matrix(base(), FaultPlan("read", on_calls=(1, 2)), max_cases=3)
    with pytest.raises(ValueError, match="Duplicate variant"):
        with_fault_matrix(base(), FaultPlan("read"), FaultPlan("read"))
    with pytest.raises(ValueError, match="fault-free"):
        with_fault_matrix(replace(base(), faults=(Fault("read", "timeout_before"),)))


def test_generated_stale_observations_require_a_declared_read_only_boundary():
    for specs in ({}, {"read": ToolSpec("mutating", {"type": "object"})}):
        with pytest.raises(ValueError, match="read-only"):
            with_fault_matrix(
                replace(base(), specifications=specs),
                FaultPlan("read", ("stale_read",), stale_value=0),
            )
