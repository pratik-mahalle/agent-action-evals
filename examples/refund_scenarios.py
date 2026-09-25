"""Run with: PYTHONPATH=src python -m agent_action_evals examples/refund_scenarios.py"""

from agent_action_evals import (
    AgentResult,
    Fault,
    Scenario,
    ScenarioVariant,
    StateEquals,
    ToolCallCount,
    ToolResponseLost,
    ToolSpec,
)


def get_order(world, arguments):
    order_id = arguments["order_id"]
    return world.get(f"orders.{order_id}")


def verify_customer(world, arguments):
    caller = world.get("caller")
    return {
        "verified": caller["verified"],
        "customer_id": caller["customer_id"],
    }


def issue_refund(world, arguments):
    order_id = arguments["order_id"]
    path = f"orders.{order_id}.refund_count"
    world.set(path, world.get(path) + 1)
    return {"status": "refunded", "order_id": order_id}


class RefundAgent:
    async def run(self, user_input, tools, context):
        order_id = "o_123"
        order = await tools.call("get_order", order_id=order_id)
        customer = await tools.call("verify_customer")
        if not customer["verified"] or customer["customer_id"] != order["customer_id"]:
            return AgentResult("Please verify your identity before I issue a refund.")

        try:
            await tools.call("issue_refund", order_id=order_id)
        except ToolResponseLost:
            # Read authoritative state before deciding whether a retry is safe.
            latest = await tools.call("get_order", order_id=order_id)
            if latest["refund_count"] == 0:
                await tools.call("issue_refund", order_id=order_id)
        return AgentResult("The refund has been issued.")


BASE_STATE = {
    "orders": {"o_123": {"customer_id": "c_7", "status": "delivered", "refund_count": 0}},
    "caller": {"customer_id": "c_7", "verified": True},
}

SCENARIOS = (
    Scenario(
        id="refund_order",
        initial_state=BASE_STATE,
        user_input="Refund order o_123",
        driver=RefundAgent(),
        handlers={
            "get_order": get_order,
            "verify_customer": verify_customer,
            "issue_refund": issue_refund,
        },
        assertions=(
            StateEquals("orders.o_123.refund_count", 1),
            ToolCallCount("issue_refund", 1),
        ),
        specifications={
            "get_order": ToolSpec(
                "Read an order by ID",
                {
                    "type": "object",
                    "properties": {"order_id": {"type": "string"}},
                    "required": ["order_id"],
                    "additionalProperties": False,
                },
                read_only=True,
            ),
            "verify_customer": ToolSpec(
                "Read the current customer's identity verification",
                {"type": "object", "properties": {}, "additionalProperties": False},
                read_only=True,
            ),
            "issue_refund": ToolSpec(
                "Issue a refund for an order",
                {
                    "type": "object",
                    "properties": {"order_id": {"type": "string"}},
                    "required": ["order_id"],
                    "additionalProperties": False,
                },
            ),
        },
        variants=(
            ScenarioVariant(
                id="unverified_caller",
                overrides={"caller.verified": False},
                assertions=(
                    StateEquals("orders.o_123.refund_count", 0),
                    ToolCallCount("issue_refund", 0),
                ),
            ),
            ScenarioVariant(
                id="response_lost_after_refund",
                overrides={},
                faults=(Fault("issue_refund", "response_lost_after_commit"),),
                assertions=(
                    StateEquals("orders.o_123.refund_count", 1),
                    ToolCallCount("issue_refund", 1),
                ),
            ),
        ),
    ),
)
