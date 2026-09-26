"""Stateful action-boundary tests for tool-using agents."""

from .boundary import ToolBoundary
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
from .verification import (
    Operation,
    RecoveryPolicy,
    ToolContract,
    ToolOutcome,
    execute_verified,
    public_tool_error,
    request_fingerprint,
)

__all__ = [
    "AgentResult",
    "Context",
    "Event",
    "Fault",
    "FaultPlan",
    "FaultTriggered",
    "Operation",
    "RecoveryPolicy",
    "RunResult",
    "Scenario",
    "ScenarioVariant",
    "StateEquals",
    "ToolCallCount",
    "ToolBoundary",
    "ToolCallOrder",
    "ToolClient",
    "ToolContract",
    "ToolOutcome",
    "ToolPrecondition",
    "ToolSpec",
    "ToolPort",
    "ToolResponseLost",
    "ToolTimeout",
    "World",
    "run_scenario",
    "execute_verified",
    "public_tool_error",
    "request_fingerprint",
    "with_fault_matrix",
]
