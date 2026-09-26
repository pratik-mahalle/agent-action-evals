"""Offline LangGraph: a real ToolNode returns verified outcomes to the agent.

Run: agent-action-evals examples/langgraph_verified_refund.py --repeat 3
"""

import json

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from verification_support import ARGUMENTS, CONTRACT, OPERATION_ID, POLICY, make_scenarios

from agent_action_evals import Operation, execute_verified, public_tool_error
from agent_action_evals.adapters.langgraph import LangGraphDriver


def build_graph(client):
    # In a deployed graph, load this persisted operation from application state.
    operation = Operation(OPERATION_ID, ARGUMENTS)

    async def refund_verified():
        outcome = await execute_verified(client, CONTRACT, operation, POLICY)
        return outcome.to_dict()

    tool = StructuredTool(
        name="refund_verified",
        description="Execute the authorized refund and independently verify its receipt. Report uncertainty explicitly.",
        args_schema={"type": "object", "properties": {}, "additionalProperties": False},
        coroutine=refund_verified,
    )

    async def agent(state):
        observations = [m for m in state["messages"] if isinstance(m, ToolMessage)]
        if not observations:
            return {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": tool.name,
                                "args": {},
                                "id": "verified-refund-call",
                            }
                        ],
                    )
                ]
            }
        # Scripted policy exposes the structured outcome without claiming success
        # for an unknown, pending, partial, or failed effect. No model API calls.
        return {"messages": [AIMessage(content=observations[-1].content)]}

    graph = StateGraph(MessagesState)
    graph.add_node("agent", agent)
    graph.add_node(
        "tools", ToolNode([tool], handle_tool_errors=lambda exc: json.dumps(public_tool_error(exc)))
    )
    graph.add_edge(START, "agent")
    graph.add_conditional_edges(
        "agent", lambda state: "tools" if state["messages"][-1].tool_calls else END
    )
    graph.add_edge("tools", "agent")
    return graph.compile()


SCENARIOS = make_scenarios(LangGraphDriver(build_graph))
