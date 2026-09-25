"""Run the reusable refund pack without model credentials or dependencies.

agent-action-evals examples/refund_pack.py --explain --text /tmp/refund-report.txt
"""

from agent_action_evals import AgentResult, ToolResponseLost, ToolTimeout
from agent_action_evals.packs import refund_scenario


class RefundAgent:
    async def run(self, user_input, tools, context):
        async def read():
            return await tools.call("get_order", order_id="o_123")

        try:
            await read()
            caller = await tools.call("verify_customer")
            if not caller["verified"]:
                return AgentResult("Please verify your identity.")
            order = await read()
        except (ToolTimeout, ToolResponseLost):
            return AgentResult("I could not verify the request. Please try later.")
        if order["customer_id"] != caller["customer_id"] or order["status"] != "delivered":
            return AgentResult("This request is not eligible for a refund.")
        if order["refund_count"]:
            return AgentResult("This order already has a refund.")
        for _ in range(2):
            try:
                await tools.call("issue_refund", order_id="o_123")
                return AgentResult("Refund confirmed.")
            except (ToolTimeout, ToolResponseLost):
                try:
                    order = await read()
                except (ToolTimeout, ToolResponseLost):
                    return AgentResult("Unable to confirm the refund. Please contact support.")
                if order["refund_count"]:
                    return AgentResult("Refund confirmed.")
        return AgentResult("Unable to confirm the refund. Please contact support.")


SCENARIOS = (refund_scenario(RefundAgent()),)
