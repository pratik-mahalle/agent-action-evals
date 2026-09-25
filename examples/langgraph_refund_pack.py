"""The same reusable pack, driven by the existing offline LangGraph policy."""

from langgraph_refund_demo import build_graph

from agent_action_evals.adapters.langgraph import LangGraphDriver
from agent_action_evals.packs import refund_scenario

SCENARIOS = (
    refund_scenario(
        LangGraphDriver(build_graph),
        scenario_id="langgraph_refund_pack",
        metadata={"mode": "offline_scripted"},
    ),
)
