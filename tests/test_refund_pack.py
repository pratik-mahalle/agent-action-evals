import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from agent_action_evals import AgentResult, ToolClient, ToolSpec, run_scenario
from agent_action_evals.cli import load_scenarios
from agent_action_evals.packs import (
    RefundBindings,
    RefundFixture,
    default_refund_fixture,
    refund_scenario,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("example", ["refund_pack.py", "langgraph_refund_pack.py"])
def test_pack_passes_on_python_and_real_langgraph_with_fresh_state(example):
    if example.startswith("langgraph"):
        pytest.importorskip("langgraph")
    scenario = load_scenarios(ROOT / "examples" / example)[0]
    assert len(scenario.variants) == 13

    async def run():
        return await asyncio.gather(
            *(
                run_scenario(scenario, variant_id=variant, seed=repeat)
                for variant in (None, *(v.id for v in scenario.variants))
                for repeat in range(2)
            )
        )

    results = asyncio.run(run())
    assert len({result.run_id for result in results}) == 28
    for result in results:
        assert result.passed, (result.case_id, result.error, result.findings)
        assert all(fault["triggered"] for fault in result.manifest["faults"])
    assert scenario.initial_state["orders"]["o_123"]["refund_count"] == 0


def test_pack_catches_blind_retry_and_points_to_second_effect():
    unsafe = load_scenarios(ROOT / "examples/refund_pack_unsafe.py")[0]
    result = asyncio.run(
        run_scenario(
            unsafe,
            variant_id="fault-issue_refund-response_lost_after_commit-call-1",
        )
    )
    assert result.status == "fail"
    assert result.error is None
    assert result.final_state["orders"]["o_123"]["refund_count"] == 2
    executed = [e for e in result.events if e.tool == "issue_refund" and e.kind == "tool_executed"]
    assert any(
        f.rule.startswith("state[") and f.event_seq == executed[1].seq for f in result.findings
    )


def test_custom_fixture_maps_tool_names_state_paths_and_schemas():
    safe = load_scenarios(ROOT / "examples/refund_pack.py")[0]
    names = {"get_order": "fetch", "verify_customer": "identify", "issue_refund": "credit"}

    def fetch(world, args):
        # This app's backing state uses different field names from its public API.
        return {
            "customer_id": world.get("purchase.owner"),
            "status": world.get("purchase.phase"),
            "refund_count": world.get("purchase.credits"),
        }

    def identity(world, args):
        return {"customer_id": world.get("actor.id"), "verified": world.get("actor.checked")}

    def credit(world, args):
        world.set("purchase.credits", world.get("purchase.credits") + 1)
        return {"status": "refunded"}

    class AdaptedAgent:
        async def run(self, user_input, tools, context):
            async def call(name, **args):
                return await tools.call(names[name], **args)

            return await safe.driver.run(user_input, ToolClient(call, {}), context)

    fixture = RefundFixture(
        initial_state={
            "purchase": {"owner": "customer-42", "phase": "delivered", "credits": 0},
            "actor": {"id": "customer-42", "checked": True},
        },
        handlers={"fetch": fetch, "identify": identity, "credit": credit},
        specifications={
            "fetch": ToolSpec("Read purchase", {"type": "object"}, read_only=True),
            "identify": ToolSpec("Read caller", {"type": "object"}, read_only=True),
            "credit": ToolSpec("Credit purchase", {"type": "object"}),
        },
        user_input="Refund this purchase",
        stale_order_value={"customer_id": "customer-42", "status": "delivered", "refund_count": 0},
        bindings=RefundBindings(
            "fetch",
            "identify",
            "credit",
            "purchase.credits",
            "purchase.owner",
            "purchase.phase",
            "actor.id",
            "actor.checked",
        ),
    )
    scenario = refund_scenario(AdaptedAgent(), fixture=fixture)
    for variant in (None, *(v.id for v in scenario.variants)):
        result = asyncio.run(run_scenario(scenario, variant_id=variant))
        assert result.passed, (variant, result.error, result.findings)
    assert fixture.initial_state["purchase"]["credits"] == 0


@pytest.mark.parametrize("variant", ["unverified_caller", "wrong_owner", "ineligible_order"])
def test_authorization_and_eligibility_mutants_fail(variant):
    class UnconditionalRefund:
        async def run(self, user_input, tools, context):
            await tools.call("issue_refund", order_id="o_123")
            return AgentResult("Refunded")

    result = asyncio.run(run_scenario(refund_scenario(UnconditionalRefund()), variant_id=variant))
    assert result.status == "fail"
    assert any(f.rule.startswith("precondition[") for f in result.findings)


def test_pack_rejects_invalid_bindings_and_fixture_baselines():
    class Unused:
        async def run(self, *args):
            raise AssertionError("Preflight must not invoke the agent")

    fixture = default_refund_fixture()
    with pytest.raises(ValueError, match="distinct"):
        refund_scenario(
            Unused(), fixture=replace(fixture, bindings=RefundBindings(refund_tool="get_order"))
        )
    fixture.initial_state["orders"]["o_123"]["refund_count"] = 1
    with pytest.raises(ValueError, match="unrefunded"):
        refund_scenario(Unused(), fixture=fixture)
