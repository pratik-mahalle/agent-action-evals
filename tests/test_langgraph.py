import asyncio

import pytest

pytest.importorskip("langgraph")
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from agent_action_evals import Scenario, StateEquals, ToolSpec, run_scenario
from agent_action_evals.adapters.langgraph import LangGraphDriver, make_tools


def graph_factory(client):
    async def agent(state):
        if len(state["messages"]) == 1:
            return {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {"name": "increment", "args": {}, "id": "call_1", "type": "tool_call"}
                        ],
                    )
                ]
            }
        return {
            "messages": [
                AIMessage(
                    content="Done",
                    usage_metadata={"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
                )
            ]
        }

    graph = StateGraph(MessagesState)
    graph.add_node("agent", agent)
    graph.add_node("tools", ToolNode(make_tools(client)))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", lambda s: "tools" if s["messages"][-1].tool_calls else END)
    graph.add_edge("tools", "agent")
    return graph.compile()


def test_real_langgraph_tool_node_reaches_world_and_reports_usage():
    scenario = Scenario(
        "langgraph",
        {"n": 0},
        "increment",
        LangGraphDriver(graph_factory),
        {"increment": lambda w, a: w.set("n", w.get("n") + 1)},
        (StateEquals("n", 1),),
        specifications={"increment": ToolSpec("Increment n", {"type": "object", "properties": {}})},
    )
    result = asyncio.run(run_scenario(scenario))
    assert result.passed, result.error
    assert result.manifest["agent_metadata"]["usage"]["total_tokens"] == 3
