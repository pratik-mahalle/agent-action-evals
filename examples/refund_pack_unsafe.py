"""Deliberately broken retry policy for demonstrating failure reports.

Only simulated tools are used. The lost-response case should exit with code 1.
"""

from refund_pack import RefundAgent

from agent_action_evals import ToolClient, ToolResponseLost
from agent_action_evals.packs import refund_scenario


class BlindRetryAgent:
    async def run(self, user_input, tools, context):
        async def call(name, **arguments):
            try:
                return await tools.call(name, **arguments)
            except ToolResponseLost:
                if name != "issue_refund":
                    raise
                # Intentional bug: retry without checking whether the refund committed.
                return await tools.call(name, **arguments)

        return await RefundAgent().run(user_input, ToolClient(call, tools.specifications), context)


SCENARIOS = (
    refund_scenario(BlindRetryAgent(), scenario_id="unsafe_refund", metadata={"mode": "mutant"}),
)
