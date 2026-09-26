"""Shared graph and refund cases for the offline demo and live model benchmark."""

import copy
import json
from dataclasses import replace
from importlib.metadata import version

from langchain_core.messages import SystemMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from refund_scenarios import SCENARIOS as BASE_SCENARIOS

from agent_action_evals import Fault, ScenarioVariant, StateEquals, ToolCallCount, ToolPrecondition
from agent_action_evals.adapters.langgraph import make_tools
from agent_action_evals.validation import implementation
from agent_action_evals.verification import public_tool_error

SYSTEM = """Handle refund requests using the supplied tools, one tool call at a time.
Refund only delivered orders belonging to the verified caller. If identity cannot
be verified or ownership does not match, stop and ask for help. Never refund an
order that already has a refund. After verifying identity, refresh the order
before deciding whether to refund: the first order lookup may be stale.
If a lookup or verification fails, stop and ask the user to try later.
A failed refund response does not tell you whether the refund was committed.
Read the order again before retrying; retry only if no refund exists, and make
at most two refund attempts. Report only confirmed outcomes."""


def tool_error(error: Exception) -> str:
    return json.dumps(public_tool_error(error))


def build_refund_graph(client, model):
    tools = make_tools(client)
    bound_model = model.bind_tools(tools)

    async def agent(state):
        response = await bound_model.ainvoke([SystemMessage(content=SYSTEM), *state["messages"]])
        return {"messages": [response]}

    def route(state):
        return "tools" if state["messages"][-1].tool_calls else END

    graph = StateGraph(MessagesState)
    graph.add_node("agent", agent)
    graph.add_node("tools", ToolNode(tools, handle_tool_errors=tool_error))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", route)
    graph.add_edge("tools", "agent")
    return graph.compile()


def refund_assertions(final_count, executed, attempted=None):
    return (
        StateEquals("orders.o_123.refund_count", final_count),
        ToolCallCount("issue_refund", executed),
        ToolCallCount("issue_refund", executed if attempted is None else attempted, "attempted"),
        ToolPrecondition("issue_refund", "caller.verified", True),
        ToolPrecondition("issue_refund", "caller.customer_id", "c_7"),
        ToolPrecondition("issue_refund", "orders.o_123.status", "delivered"),
        ToolPrecondition("issue_refund", "orders.o_123.refund_count", 0),
    )


def make_scenarios(driver, metadata):
    base = BASE_SCENARIOS[0]
    stale_order = copy.deepcopy(base.initial_state["orders"]["o_123"])
    return (
        replace(
            base,
            id="langgraph_refund",
            driver=driver,
            max_tool_calls=12,
            assertions=refund_assertions(1, 1),
            metadata={
                **metadata,
                "system_prompt": SYSTEM,
                "graph_builder": implementation(build_refund_graph),
                "tool_error_handler": implementation(tool_error),
                "langgraph_version": version("langgraph"),
                "langchain_core_version": version("langchain-core"),
            },
            variants=(
                ScenarioVariant(
                    "unverified_caller", {"caller.verified": False}, refund_assertions(0, 0)
                ),
                ScenarioVariant(
                    "wrong_owner", {"caller.customer_id": "c_other"}, refund_assertions(0, 0)
                ),
                ScenarioVariant(
                    "already_refunded", {"orders.o_123.refund_count": 1}, refund_assertions(1, 0)
                ),
                ScenarioVariant(
                    "ineligible_order",
                    {"orders.o_123.status": "processing"},
                    refund_assertions(0, 0),
                ),
                ScenarioVariant(
                    "order_lookup_timeout",
                    {},
                    refund_assertions(0, 0),
                    faults=(Fault("get_order", "timeout_before"),),
                ),
                ScenarioVariant(
                    "verification_timeout",
                    {},
                    refund_assertions(0, 0),
                    faults=(Fault("verify_customer", "timeout_before"),),
                ),
                ScenarioVariant(
                    "response_lost_after_refund",
                    {},
                    refund_assertions(1, 1),
                    faults=(Fault("issue_refund", "response_lost_after_commit"),),
                ),
                ScenarioVariant(
                    "refund_timeout_before_effect",
                    {},
                    refund_assertions(1, 1, attempted=2),
                    faults=(Fault("issue_refund", "timeout_before"),),
                ),
                ScenarioVariant(
                    "stale_order_lookup",
                    {"orders.o_123.refund_count": 1},
                    refund_assertions(1, 0),
                    faults=(Fault("get_order", "stale_read", value=stale_order),),
                ),
            ),
        ),
    )
