"""Stateful action-boundary tests for tool-using agents."""

from .core import (
    AgentResult,
    Context,
    Event,
    Fault,
    Scenario,
    ScenarioVariant,
    StateEquals,
    ToolCallCount,
    ToolCallOrder,
    ToolClient,
    ToolPort,
    ToolPrecondition,
    ToolResponseLost,
    ToolSpec,
    ToolTimeout,
    World,
)
from .faults import FaultPlan, FaultTriggered, with_fault_matrix
from .runner import RunResult, run_scenario

__all__ = [
    "AgentResult",
    "Context",
    "Event",
    "Fault",
    "FaultPlan",
    "FaultTriggered",
    "RunResult",
    "Scenario",
    "ScenarioVariant",
    "StateEquals",
    "ToolCallCount",
    "ToolCallOrder",
    "ToolClient",
    "ToolPrecondition",
    "ToolSpec",
    "ToolPort",
    "ToolResponseLost",
    "ToolTimeout",
    "World",
    "run_scenario",
    "with_fault_matrix",
]
