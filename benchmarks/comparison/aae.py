"""Declare the comparison contract using Agent Action Evals' existing runner."""

from cases import HANDLERS, ResponseLost, ScriptedPolicy

from agent_action_evals import (
    AgentResult,
    Fault,
    Scenario,
    StateEquals,
    ToolCallCount,
    ToolPrecondition,
    ToolResponseLost,
    ToolSpec,
    run_scenario,
)


class Driver:
    def __init__(self, policy):
        self.policy = policy

    async def run(self, user_input, tools, context):
        class Client:
            async def call(self, name):
                try:
                    return await tools.call(name)
                except ToolResponseLost as error:
                    raise ResponseLost from error

        return AgentResult(await self.policy.run(Client()))


async def evaluate(case):
    faults = ()
    if case.fault == "stale_read":
        faults = (Fault("get_order", "stale_read", value=case.stale_order()),)
    elif case.fault:
        faults = (Fault("issue_refund", case.fault),)
    scenario = Scenario(
        id=case.id,
        initial_state=case.state(),
        user_input="Refund the order",
        driver=Driver(ScriptedPolicy(case.family, case.mutant)),
        handlers=HANDLERS,
        assertions=(
            StateEquals("order.refunds", case.expected_refunds),
            ToolCallCount("issue_refund", case.expected_executions),
            ToolPrecondition("issue_refund", "order.refunds", 0),
            ToolPrecondition("issue_refund", "caller.id", "c_7"),
            ToolPrecondition("issue_refund", "caller.verified", True),
        ),
        specifications={
            name: ToolSpec(
                name,
                {"type": "object", "properties": {}, "additionalProperties": False},
                read_only=name != "issue_refund",
            )
            for name in HANDLERS
        },
        faults=faults,
    )
    return await run_scenario(scenario)
