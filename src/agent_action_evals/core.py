from __future__ import annotations

import asyncio
import copy
import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol

from jsonschema import Draft202012Validator

from .state import World, canonical, changes, same


class ToolTimeout(Exception):
    """Simulated timeout before execution."""


class ToolResponseLost(Exception):
    """Tool executed, but its response was lost."""


class UnknownTool(Exception):
    pass


class ToolLimitExceeded(Exception):
    pass


class ToolSchemaError(Exception):
    pass


@dataclass(frozen=True)
class Event:
    seq: int
    kind: str
    tool: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Finding:
    rule: str
    expected: Any
    actual: Any
    event_seq: int | None = None
    attribution: str = "unavailable"


@dataclass(frozen=True)
class Fault:
    tool: str
    kind: str
    on_call: int = 1
    value: Any = None

    def __post_init__(self) -> None:
        if self.kind not in {"timeout_before", "response_lost_after_commit", "stale_read"}:
            raise ValueError(f"Unknown fault kind: {self.kind}")
        if type(self.on_call) is not int or self.on_call < 1:
            raise ValueError("on_call must be a positive integer")
        canonical(self.value)


@dataclass(frozen=True)
class ToolSpec:
    description: str
    parameters: Mapping[str, Any]
    result_schema: Mapping[str, Any] | None = None
    read_only: bool = False
    version: str = "1"


ToolHandler = Callable[[World, dict[str, Any]], Any]


class ToolClient:
    """Agent-facing interface. The oracle stays in the runner, outside sandboxed agents."""

    def __init__(self, call: Callable, specifications: Mapping[str, ToolSpec]):
        self.__call = call
        self.specifications = copy.deepcopy(dict(specifications))

    async def call(self, name: str, **arguments: Any) -> Any:
        return await self.__call(name, **arguments)


class ToolPort:
    def __init__(
        self,
        world: World,
        handlers: Mapping[str, ToolHandler],
        faults: tuple[Fault, ...] = (),
        max_calls: int = 50,
        specifications: Mapping[str, ToolSpec] | None = None,
        preconditions: tuple[ToolPrecondition, ...] = (),
    ):
        self.world = world
        self.handlers = dict(handlers)
        self.specifications = dict(specifications or {})
        self.faults = faults
        self.preconditions = preconditions
        self.events: list[Event] = []
        self.violations: list[Finding] = []
        self._counts: dict[str, int] = {}
        self._max_calls = max_calls
        self._lock = asyncio.Lock()
        self._sealed = False

    def client(self) -> ToolClient:
        return ToolClient(self.call, self.specifications)

    def seal(self) -> None:
        self._sealed = True
        self.world.seal()

    def record(self, kind: str, tool: str | None = None, **details: Any) -> Event:
        event = Event(len(self.events) + 1, kind, tool, copy.deepcopy(details))
        self.events.append(event)
        return event

    def reject(self, name: str, rule: str, error_type: type[Exception]) -> None:
        event = self.record("tool_rejected", name, reason=rule)
        self.violations.append(
            Finding(rule, "permitted call", "rejected call", event.seq, "direct")
        )
        raise error_type(rule)

    async def call(self, name: str, **arguments: Any) -> Any:
        async with self._lock:
            if self._sealed:
                raise RuntimeError("Tool port is sealed; run has ended")
            return await self._call_locked(name, arguments)

    async def _call_locked(self, name: str, arguments: dict[str, Any]) -> Any:
        self._counts[name] = self._counts.get(name, 0) + 1
        call_number = self._counts[name]
        self.record("tool_attempt", name, arguments=arguments, call_number=call_number)
        if sum(self._counts.values()) > self._max_calls:
            self.reject(name, "tool-call limit exceeded", ToolLimitExceeded)
        if name not in self.handlers:
            self.reject(name, "unknown tool", UnknownTool)
        spec = self.specifications.get(name)
        if spec and not Draft202012Validator(spec.parameters).is_valid(arguments):
            self.reject(name, "tool argument schema violation", ToolSchemaError)
        canonical(arguments)
        fault = next((f for f in self.faults if f.tool == name and f.on_call == call_number), None)
        if fault and fault.kind == "timeout_before":
            self.record("tool_fault", name, fault_kind=fault.kind, call_number=call_number)
            raise ToolTimeout(name)
        if fault and fault.kind == "stale_read":
            self.record("tool_fault", name, fault_kind=fault.kind, call_number=call_number)
            self.record("tool_observation", name, result=fault.value)
            return copy.deepcopy(fault.value)

        for precondition in self.preconditions:
            if precondition.tool == name:
                try:
                    actual = self.world.get(precondition.path)
                except KeyError:
                    actual = {"missing": True}
                if not same(actual, precondition.value):
                    self.record("precondition_failed", name, path=precondition.path, actual=actual)
        before = self.world.snapshot()
        status = "completed"
        try:
            result = self.handlers[name](self.world, copy.deepcopy(arguments))
            if inspect.isawaitable(result):
                result = await result
            canonical(result)
        except BaseException as exc:
            status = "cancelled" if isinstance(exc, asyncio.CancelledError) else "error"
            self.record("tool_error", name, error_type=type(exc).__name__)
            raise
        finally:
            delta = changes(before, self.world.snapshot())
            event = self.record("tool_executed", name, changes=delta, status=status)
            if spec and spec.read_only and delta:
                self.violations.append(
                    Finding("read-only tool changed state", {}, delta, event.seq, "direct")
                )
        if spec and spec.result_schema is not None:
            if not Draft202012Validator(spec.result_schema).is_valid(result):
                self.reject(name, "tool result schema violation", ToolSchemaError)
        if fault and fault.kind == "response_lost_after_commit":
            self.record("tool_fault", name, fault_kind=fault.kind, call_number=call_number)
            raise ToolResponseLost(name)
        self.record("tool_observation", name, result=result)
        return copy.deepcopy(result)


@dataclass(frozen=True)
class AgentResult:
    text: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Context:
    run_id: str
    case_id: str
    seed: int = 0


class AgentDriver(Protocol):
    async def run(self, user_input: str, tools: ToolClient, context: Context) -> AgentResult: ...


class Assertion(Protocol):
    def evaluate(self, world: World, events: list[Event]) -> Finding | None: ...


@dataclass(frozen=True)
class StateEquals:
    path: str
    value: Any

    def evaluate(self, world: World, events: list[Event]) -> Finding | None:
        try:
            actual = world.get(self.path)
        except KeyError:
            return Finding(f"state[{self.path}]", self.value, {"missing": True})
        if same(actual, self.value):
            return None
        related = next(
            (
                e.seq
                for e in reversed(events)
                if self.path in e.details.get("changes", {})
                and same(e.details["changes"][self.path]["before"], self.value)
                and not same(e.details["changes"][self.path]["after"], self.value)
            ),
            None,
        )
        return Finding(
            f"state[{self.path}]",
            self.value,
            actual,
            related,
            "direct_transition" if related else "unavailable",
        )


@dataclass(frozen=True)
class ToolCallCount:
    tool: str
    count: int
    phase: str = "executed"

    def evaluate(self, world: World, events: list[Event]) -> Finding | None:
        kind = "tool_executed" if self.phase == "executed" else "tool_attempt"
        matching = [e for e in events if e.kind == kind and e.tool == self.tool]
        if len(matching) == self.count:
            return None
        seq = matching[self.count].seq if len(matching) > self.count else None
        return Finding(
            f"{self.phase} calls[{self.tool}]",
            self.count,
            len(matching),
            seq,
            "direct" if seq else "unavailable",
        )


@dataclass(frozen=True)
class ToolCallOrder:
    tools: tuple[str, ...]

    def evaluate(self, world: World, events: list[Event]) -> Finding | None:
        executed = [e for e in events if e.kind == "tool_executed"]
        actual = tuple(e.tool for e in executed)
        if actual == self.tools:
            return None
        index = next(
            (i for i, (a, b) in enumerate(zip(actual, self.tools)) if a != b),
            min(len(actual), len(self.tools)),
        )
        seq = executed[index].seq if index < len(executed) else None
        return Finding(
            "executed tool order", self.tools, actual, seq, "direct" if seq else "unavailable"
        )


@dataclass(frozen=True)
class ToolPrecondition:
    """Observe state before a tool executes; violations are recorded, not blocked."""

    tool: str
    path: str
    value: Any

    def evaluate(self, world: World, events: list[Event]) -> Finding | None:
        for event in events:
            if event.kind == "precondition_failed" and event.tool == self.tool:
                if event.details["path"] == self.path:
                    return Finding(
                        f"precondition[{self.tool}:{self.path}]",
                        self.value,
                        event.details["actual"],
                        event.seq,
                        "direct",
                    )
        return None


@dataclass(frozen=True)
class ScenarioVariant:
    id: str
    overrides: Mapping[str, Any]
    assertions: tuple[Assertion, ...]
    faults: tuple[Fault, ...] = ()


@dataclass(frozen=True)
class Scenario:
    id: str
    initial_state: Mapping[str, Any]
    user_input: str
    driver: AgentDriver
    handlers: Mapping[str, ToolHandler]
    assertions: tuple[Assertion, ...]
    variants: tuple[ScenarioVariant, ...] = ()
    faults: tuple[Fault, ...] = ()
    max_tool_calls: int = 50
    specifications: Mapping[str, ToolSpec] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
