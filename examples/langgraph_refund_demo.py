"""Offline LangGraph demo: scripted model messages, real graph and tool execution.

Run: agent-action-evals examples/langgraph_refund_demo.py --repeat 3
"""

import json

from langchain_core.messages import AIMessage, ToolMessage
from langgraph_support import build_refund_graph, make_scenarios

from agent_action_evals.adapters.langgraph import LangGraphDriver
from agent_action_evals.validation import implementation


class ScriptedRefundModel:
    """Fixed policy for order o_123; sees messages, never fixtures or assertions."""

    def bind_tools(self, tools):
        self.tool_names = {tool.name for tool in tools}
        return self

    async def ainvoke(self, messages):
        observations = [m for m in messages if isinstance(m, ToolMessage)]

        def call(name):
            if name not in self.tool_names:
                raise ValueError(f"Missing tool: {name}")
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": name,
                        "args": {} if name == "verify_customer" else {"order_id": "o_123"},
                        "id": f"call_{len(observations) + 1}",
                        "type": "tool_call",
                    }
                ],
            )

        if not observations:
            return call("get_order")
        last = observations[-1]
        if last.status == "error":
            if last.name == "issue_refund":
                return call("get_order")
            return AIMessage(content="I could not verify the request. Please try again later.")

        value = json.loads(last.content)
        if last.name == "issue_refund":
            return AIMessage(content="The refund has been issued.")
        if last.name == "verify_customer":
            if not value["verified"]:
                return AIMessage(content="Please verify your identity before requesting a refund.")
            return call("get_order")

        if value["refund_count"] > 0:
            return AIMessage(content="The order already has a refund. No additional refund issued.")
        if value["status"] != "delivered":
            return AIMessage(content="This order is not eligible for a refund.")
        verified = [m for m in observations if m.name == "verify_customer" and m.status != "error"]
        if not verified:
            return call("verify_customer")
        customer = json.loads(verified[-1].content)
        if not customer["verified"] or customer["customer_id"] != value["customer_id"]:
            return AIMessage(content="The verified customer does not own this order.")
        if sum(m.name == "issue_refund" for m in observations) >= 2:
            return AIMessage(content="I could not confirm the refund. Please contact support.")
        return call("issue_refund")


def build_graph(client):
    return build_refund_graph(client, ScriptedRefundModel())


SCENARIOS = make_scenarios(
    LangGraphDriver(build_graph),
    {
        "mode": "offline_scripted",
        "model": "ScriptedRefundModel",
        "model_implementation": implementation(ScriptedRefundModel()),
    },
)
