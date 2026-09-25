import asyncio
import unittest

from agent_action_evals import (
    AgentResult,
    Fault,
    Scenario,
    ScenarioVariant,
    StateEquals,
    ToolCallCount,
    ToolResponseLost,
    ToolTimeout,
    run_scenario,
)

INITIAL = {"orders": {"o_1": {"refund_count": 0}}, "caller": {"verified": True}}


def refund(world, arguments):
    path = "orders.o_1.refund_count"
    world.set(path, world.get(path) + 1)
    return {"ok": True}


def read_order(world, arguments):
    return world.get("orders.o_1")


class UnsafeRetryAgent:
    async def run(self, user_input, tools, context):
        try:
            await tools.call("refund", order_id="o_1")
        except ToolResponseLost:
            await tools.call("refund", order_id="o_1")
        return AgentResult("Done")


class CautiousAgent:
    async def run(self, user_input, tools, context):
        try:
            await tools.call("refund", order_id="o_1")
        except ToolResponseLost:
            order = await tools.call("read_order", order_id="o_1")
            if order["refund_count"] == 0:
                await tools.call("refund", order_id="o_1")
        return AgentResult("Done")


class BoundaryAgent:
    async def run(self, user_input, tools, context):
        if not await tools.call("verified"):
            return AgentResult("Please verify first")
        await tools.call("refund", order_id="o_1")
        return AgentResult("Done")


class FaultAwareAgent:
    async def run(self, user_input, tools, context):
        try:
            await tools.call("read_order", order_id="o_1")
        except ToolTimeout:
            return AgentResult("Could not verify order")
        return AgentResult("Read order")


def scenario(driver, *, faults=(), variants=(), assertions=None):
    return Scenario(
        id="refund",
        initial_state=INITIAL,
        user_input="Refund order o_1",
        driver=driver,
        handlers={
            "refund": refund,
            "read_order": read_order,
            "verified": lambda world, arguments: world.get("caller.verified"),
        },
        assertions=assertions
        or (
            StateEquals("orders.o_1.refund_count", 1),
            ToolCallCount("refund", 1),
        ),
        faults=faults,
        variants=variants,
    )


class RunnerTests(unittest.TestCase):
    def test_unsafe_retry_fails_on_actual_duplicate_effect(self):
        result = asyncio.run(
            run_scenario(
                scenario(
                    UnsafeRetryAgent(),
                    faults=(Fault("refund", "response_lost_after_commit"),),
                )
            )
        )
        self.assertFalse(result.passed)
        self.assertEqual(result.final_state["orders"]["o_1"]["refund_count"], 2)
        self.assertEqual(len([e for e in result.events if e.kind == "tool_executed"]), 2)
        self.assertTrue(any(f.rule == "state[orders.o_1.refund_count]" for f in result.findings))
        executed = [e for e in result.events if e.kind == "tool_executed"]
        state_finding = next(f for f in result.findings if f.rule.startswith("state["))
        self.assertEqual(state_finding.event_seq, executed[1].seq)

    def test_read_before_retry_avoids_duplicate_effect(self):
        result = asyncio.run(
            run_scenario(
                scenario(
                    CautiousAgent(),
                    faults=(Fault("refund", "response_lost_after_commit"),),
                )
            )
        )
        self.assertTrue(result.passed)
        self.assertEqual(result.final_state["orders"]["o_1"]["refund_count"], 1)

    def test_paired_variant_has_fresh_state(self):
        item = scenario(
            BoundaryAgent(),
            variants=(
                ScenarioVariant(
                    "unverified",
                    {"caller.verified": False},
                    (StateEquals("orders.o_1.refund_count", 0), ToolCallCount("refund", 0)),
                ),
            ),
        )
        base = asyncio.run(run_scenario(item))
        variant = asyncio.run(run_scenario(item, variant_id="unverified"))
        self.assertTrue(base.passed)
        self.assertTrue(variant.passed)
        self.assertEqual(INITIAL["orders"]["o_1"]["refund_count"], 0)

    def test_timeout_before_execution_records_no_effect(self):
        result = asyncio.run(
            run_scenario(
                scenario(
                    FaultAwareAgent(),
                    faults=(Fault("read_order", "timeout_before"),),
                    assertions=(
                        StateEquals("orders.o_1.refund_count", 0),
                        ToolCallCount("read_order", 0),
                        ToolCallCount("read_order", 1, phase="attempted"),
                    ),
                )
            )
        )
        self.assertTrue(result.passed)

    def test_stale_read_is_observation_without_execution(self):
        result = asyncio.run(
            run_scenario(
                scenario(
                    FaultAwareAgent(),
                    faults=(Fault("read_order", "stale_read", value={"refund_count": 9}),),
                    assertions=(
                        StateEquals("orders.o_1.refund_count", 0),
                        ToolCallCount("read_order", 0),
                    ),
                )
            )
        )
        self.assertTrue(result.passed)
        self.assertTrue(any(e.kind == "tool_observation" for e in result.events))


if __name__ == "__main__":
    unittest.main()
