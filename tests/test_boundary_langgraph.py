import asyncio
import json
from typing import Annotated

import pytest

pytest.importorskip("langgraph")
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import InjectedState, ToolNode, ToolRuntime
from langgraph.types import Command

from agent_action_evals import Fault, ToolBoundary
from agent_action_evals.adapters.langgraph import wrap_tool_node


def call(name, args=None, call_id="model_call_1"):
    return {"name": name, "args": args or {}, "id": call_id, "type": "tool_call"}


def graph_for(node):
    class State(MessagesState):
        marker: str

    graph = StateGraph(State)
    graph.add_node("tools", node)
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    return graph.compile()


def invoke_node(node, calls, *, asynchronous=False):
    graph = graph_for(node)
    state = {"messages": [AIMessage(content="", tool_calls=calls)]}
    result = asyncio.run(graph.ainvoke(state)) if asynchronous else graph.invoke(state)
    return {"messages": [m for m in result["messages"] if isinstance(m, ToolMessage)]}


@pytest.mark.parametrize("asynchronous", [False, True])
def test_native_schema_artifact_state_and_config_preserved(asynchronous):
    artifact = object()
    observed = []

    @tool(response_format="content_and_artifact")
    def inspect_order(order: str, state: Annotated[dict, InjectedState], runtime: ToolRuntime):
        """Inspect an order using runtime context."""
        observed.append((state["marker"], runtime.config["configurable"]["tenant"]))
        return order, artifact

    boundary = ToolBoundary(capture_payloads=True)
    node = wrap_tool_node([inspect_order], boundary, tags=["existing"])
    assert node.tools_by_name["inspect_order"] is inspect_order
    assert node.tools_by_name["inspect_order"].args_schema is inspect_order.args_schema
    request = {
        "messages": [AIMessage(content="", tool_calls=[call("inspect_order", {"order": "o1"})])],
        "marker": "state",
    }
    config = {"configurable": {"tenant": "tenant"}}
    graph = graph_for(node)
    result = (
        asyncio.run(graph.ainvoke(request, config))
        if asynchronous
        else graph.invoke(request, config)
    )
    message = result["messages"][-1]
    assert message.content == "o1" and message.artifact is artifact
    assert message.tool_call_id == "model_call_1" and message.name == "inspect_order"
    assert observed == [("state", "tenant")]
    assert node.tags == ["existing"]
    attempt = boundary.events[0].details
    assert attempt["arguments"] == {"args": [], "kwargs": {"order": "o1"}}
    assert "tenant" not in json.dumps(boundary.report(include_payloads=True))


@pytest.mark.parametrize("asynchronous", [False, True])
def test_toolnode_timeout_messages_match_and_caller_retry_is_visible(asynchronous):
    errors = []
    for kind in ("timeout_before", "response_lost_after_commit"):
        effects = []

        @tool
        def create_issue(title: str):
            """Create an issue."""
            effects.append(title)
            return {"number": len(effects)}

        boundary = ToolBoundary((Fault("create_issue", kind),), capture_payloads=True)
        node = wrap_tool_node([create_issue], boundary, handle_tool_errors=True)

        def invoke(calls):
            return invoke_node(node, calls, asynchronous=asynchronous)

        first = invoke([call("create_issue", {"title": "bug"})])["messages"][0]
        assert first.status == "error"
        errors.append(first.content)
        assert effects == ([] if kind == "timeout_before" else ["bug"])
        second = invoke([call("create_issue", {"title": "bug"}, "retry_2")])["messages"][0]
        assert second.status == "success"
        assert len(effects) == (1 if kind == "timeout_before" else 2)
        attempts = [e for e in boundary.events if e.kind == "tool_attempt"]
        assert attempts[1].details["same_input_as"] == 1
        assert attempts[1].details["source_call_id"] == "retry_2"
    assert errors[0] == errors[1]


def test_async_tool_and_command_stay_native():
    @tool
    async def mark_done(runtime: ToolRuntime) -> Command:
        """Mark the workflow complete."""
        await asyncio.sleep(0)
        return Command(
            update={"messages": [ToolMessage(content="done", tool_call_id=runtime.tool_call_id)]}
        )

    boundary = ToolBoundary()
    graph = StateGraph(MessagesState)
    graph.add_node("tools", wrap_tool_node([mark_done], boundary))
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    result = asyncio.run(
        graph.compile().ainvoke(
            {"messages": [AIMessage(content="", tool_calls=[call("mark_done")])]}
        )
    )
    assert result["messages"][-1].content == "done"
    assert result["messages"][-1].tool_call_id == "model_call_1"
    assert len([e for e in boundary.events if e.kind == "tool_executed"]) == 1


def test_stale_read_is_tool_message_without_calling_original():
    calls = []

    @tool
    def lookup():
        """Find issues."""
        calls.append(1)
        return {"issues": [1]}

    boundary = ToolBoundary((Fault("lookup", "stale_read", value={"issues": []}),))
    with pytest.raises(ValueError, match="read-only"):
        wrap_tool_node([lookup], boundary)
    node = wrap_tool_node([lookup], boundary, read_only={"lookup"})
    result = invoke_node(node, [call("lookup")])["messages"][0]
    assert json.loads(result.content) == {"issues": []}
    assert result.tool_call_id == "model_call_1" and calls == []


def test_validation_and_native_error_handling_are_not_masked_as_response_loss():
    failure = ValueError("provider failure")

    @tool
    def tool_error(value: int):
        """Raise a real error."""
        raise failure

    boundary = ToolBoundary((Fault("tool_error", "response_lost_after_commit"),))
    node = wrap_tool_node([tool_error], boundary, handle_tool_errors=True)
    inputs = [call("tool_error", {"value": 1})]
    native = invoke_node(ToolNode([tool_error], handle_tool_errors=True), inputs)["messages"][0]
    result = invoke_node(node, inputs)["messages"][0]
    assert result.content == native.content and result.status == native.status == "error"
    assert not boundary.report()["faults"][0]["triggered"]
    raw = wrap_tool_node([tool_error], ToolBoundary(), handle_tool_errors=False)
    with pytest.raises(ValueError) as caught:
        invoke_node(raw, inputs)
    assert caught.value is failure
    invalid = [call("tool_error", {"value": "not an integer"})]
    assert invoke_node(node, invalid)["messages"][0].status == "error"
    unknown = [call("missing")]
    assert (
        invoke_node(node, unknown)["messages"][0].content
        == invoke_node(ToolNode([tool_error]), unknown)["messages"][0].content
    )


def test_fault_typo_fails_before_running_tools():
    @tool
    def existing():
        """An existing tool."""
        raise AssertionError("Should not run")

    with pytest.raises(ValueError, match="unregistered"):
        wrap_tool_node([existing], ToolBoundary((Fault("typo", "timeout_before"),)))
