"""Generate independent, single-fault variants without invoking an agent."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field, replace
from typing import Any, Mapping

from .core import Assertion, Fault, Finding, Scenario, ScenarioVariant
from .validation import digest, prepare_case

_UNSET = object()


@dataclass(frozen=True)
class FaultTriggered:
    """Require an injection to fire; an unexercised fault is not a passing test."""

    tool: str
    kind: str
    on_call: int = 1

    def __post_init__(self):
        Fault(self.tool, self.kind, self.on_call)

    def evaluate(self, world, events):
        for event in events:
            if (
                event.kind == "tool_fault"
                and event.tool == self.tool
                and event.details.get("fault_kind") == self.kind
                and event.details.get("call_number") == self.on_call
            ):
                return None
        return Finding(
            f"fault coverage[{self.tool}:{self.kind}:call-{self.on_call}]",
            "injected",
            "not reached",
        )


@dataclass(frozen=True)
class FaultPlan:
    """Expand kinds × call occurrences into separate variants.

    Assertions default to the base case. Supply different assertions when the
    correct response to a fault is to stop. Stale observations must be explicit.
    """

    tool: str
    kinds: tuple[str, ...] = ("timeout_before", "response_lost_after_commit")
    on_calls: tuple[int, ...] = (1,)
    assertions: tuple[Assertion, ...] | None = None
    overrides: Mapping[str, Any] = field(default_factory=dict)
    stale_value: Any = _UNSET


def with_fault_matrix(scenario: Scenario, *plans: FaultPlan, max_cases: int = 100) -> Scenario:
    """Return a new scenario with stable, selectable single-fault case IDs.

    Existing variants are preserved. Generated cases branch from the base
    fixture, not from each other. No model calls or baseline recording occur.
    """
    if type(max_cases) is not int or max_cases < 1:
        raise ValueError("max_cases must be a positive integer")
    if scenario.faults:
        raise ValueError("Fault matrices require a fault-free base scenario")
    prepare_case(scenario, None)
    total = sum(len(plan.kinds) * len(plan.on_calls) for plan in plans)
    if total > max_cases:
        raise ValueError(f"Fault matrix would generate {total} cases; limit is {max_cases}")
    variants = list(scenario.variants)
    for plan in plans:
        if not plan.kinds or not plan.on_calls:
            raise ValueError("Fault plans require kinds and on_calls")
        if len(set(plan.kinds)) != len(plan.kinds) or len(set(plan.on_calls)) != len(plan.on_calls):
            raise ValueError("Fault plans must not repeat kinds or call occurrences")
        if "stale_read" in plan.kinds:
            spec = scenario.specifications.get(plan.tool)
            if spec is None or not spec.read_only:
                raise ValueError("Generated stale_read cases require a read-only ToolSpec")
            if plan.stale_value is _UNSET:
                raise ValueError("stale_read requires an explicit stale_value")
        for kind in plan.kinds:
            for occurrence in plan.on_calls:
                fault = Fault(
                    plan.tool,
                    kind,
                    occurrence,
                    copy.deepcopy(plan.stale_value) if kind == "stale_read" else None,
                )
                if occurrence > scenario.max_tool_calls:
                    raise ValueError("Fault occurrence exceeds the scenario tool-call budget")
                case_id = f"fault-{plan.tool}-{kind}-call-{occurrence}"
                if len(case_id) > 128:
                    case_id = case_id[:110] + "-" + digest(case_id)[:12]
                assertions = scenario.assertions if plan.assertions is None else plan.assertions
                if not assertions:
                    raise ValueError("Generated cases require business assertions")
                variants.append(
                    ScenarioVariant(
                        case_id,
                        copy.deepcopy(dict(plan.overrides)),
                        tuple(assertions) + (FaultTriggered(plan.tool, kind, occurrence),),
                        (fault,),
                    )
                )
    generated = replace(scenario, variants=tuple(variants))
    prepare_case(generated, None)
    return generated
