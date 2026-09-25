"""Live opt-in benchmark. Set ANTHROPIC_API_KEY and AAE_MODEL before running."""

import os
from importlib.metadata import version

from langchain_anthropic import ChatAnthropic
from langgraph_support import build_refund_graph, make_scenarios

from agent_action_evals.adapters.langgraph import LangGraphDriver

MODEL = os.environ.get("AAE_MODEL")
if not MODEL or not os.environ.get("ANTHROPIC_API_KEY"):
    raise ValueError(
        "Set AAE_MODEL and ANTHROPIC_API_KEY to run the live example. "
        "For an offline run, use examples/langgraph_refund_demo.py"
    )


def build_graph(client):
    model = ChatAnthropic(
        model=MODEL,
        temperature=0,
        max_tokens=1024,
        default_request_timeout=20,
        max_retries=0,
    )
    return build_refund_graph(client, model)


SCENARIOS = make_scenarios(
    LangGraphDriver(build_graph),
    {
        "mode": "live",
        "provider": "anthropic",
        "model": MODEL,
        "temperature": 0,
        "max_tokens": 1024,
        "request_timeout_seconds": 20,
        "max_retries": 0,
        "langchain_anthropic_version": version("langchain-anthropic"),
    },
)
