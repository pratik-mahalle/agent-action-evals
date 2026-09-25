"""Optional integration: factories bind an existing graph's tools for each run."""

from dataclasses import dataclass
from typing import Callable

from ..core import AgentResult


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
