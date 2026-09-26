"""Optional integration: factories bind an existing graph's tools for each run."""

import inspect
import json
from dataclasses import dataclass
from typing import Callable

from ..core import AgentResult


def wrap_tool_node(tools, boundary, *, read_only=(), **node_options):
    """Build a ToolNode around existing tools with a per-run ToolBoundary.

    Original tools, schemas, config injection, error handling and outputs stay
    owned by LangGraph. Supply both sync and async hooks; neither retries calls.
    """
    from langchain_core.messages import ToolMessage
    from langgraph.prebuilt import ToolNode

    if not {"wrap_tool_call", "awrap_tool_call"} <= inspect.signature(ToolNode).parameters.keys():
        raise ImportError("Update langgraph-prebuilt to a version with ToolNode wrapper hooks")
    if {"wrap_tool_call", "awrap_tool_call"} & node_options.keys():
        raise ValueError("wrap_tool_node owns the two ToolNode wrapper hooks")

    def view(result):
        if isinstance(result, ToolMessage):
            return {
                "content": result.content,
                "artifact": result.artifact,
                "status": result.status,
                "tool_call_id": result.tool_call_id,
            }
        return result

    def options(request):
        call = request.tool_call

        def stale(value):
            return ToolMessage(
                content=value if isinstance(value, str) else json.dumps(value, ensure_ascii=False),
                tool_call_id=call["id"],
                name=call["name"],
            )

        return {
            "source_call_id": call["id"],
            "stale": stale,
            "view": view,
            "is_error": lambda result: isinstance(result, ToolMessage) and result.status == "error",
        }

    def wrap(request, execute):
        call = request.tool_call
        if request.tool is None:
            return execute(request)  # Leave native unknown-tool validation intact.
        return boundary._call(
            call["name"], (), call["args"], lambda: execute(request), **options(request)
        )

    async def awrap(request, execute):
        call = request.tool_call
        if request.tool is None:
            return await execute(request)
        return await boundary._acall(
            call["name"], (), call["args"], lambda: execute(request), **options(request)
        )

    node = ToolNode(tools, wrap_tool_call=wrap, awrap_tool_call=awrap, **node_options)
    names = set(node.tools_by_name)
    read_only = set(read_only)
    if read_only - names:
        raise ValueError("read_only contains an unregistered tool")
    if any(f.tool not in names for f in boundary._faults):
        raise ValueError("A fault targets an unregistered ToolNode tool")
    for name in names:
        boundary._validate(name, name in read_only)
    return node


def make_tools(client):
    from langchain_core.tools import StructuredTool

    def wrap(name, spec):
        async def invoke(**arguments):
            return await client.call(name, **arguments)

        return StructuredTool(
            name=name,
            description=spec.description,
            args_schema=dict(spec.parameters),
            coroutine=invoke,
        )

    return [wrap(name, spec) for name, spec in client.specifications.items()]


@dataclass
class LangGraphDriver:
    graph_factory: Callable
    isolation = "trusted_in_process"

    def manifest(self):
        from ..validation import implementation

        return {"factory": implementation(self.graph_factory)}

    async def run(self, user_input, tools, context):
        from langchain_core.messages import HumanMessage

        graph = self.graph_factory(tools)
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content=user_input)]},
            config={"configurable": {"thread_id": context.run_id}, "recursion_limit": 100},
        )
        messages = result["messages"]
        content = messages[-1].content
        text = (
            content
            if isinstance(content, str)
            else "".join(block.get("text", "") for block in content if isinstance(block, dict))
        )
        usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        calls = 0
        for message in messages:
            if getattr(message, "type", None) == "ai":
                calls += 1
                tokens = getattr(message, "usage_metadata", None) or {}
                for key in usage:
                    usage[key] += tokens.get(key, 0)
        return AgentResult(text, {"model_calls": calls, "usage": usage})
