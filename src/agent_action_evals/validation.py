from __future__ import annotations

import copy
import hashlib
import inspect
import re
from dataclasses import asdict, is_dataclass
from functools import lru_cache
from typing import Any

from jsonschema import Draft202012Validator

from .core import Scenario, StateEquals, ToolCallCount, ToolCallOrder, ToolPrecondition
from .state import canonical, select


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


@lru_cache(maxsize=1024)
def _implementation(target) -> tuple[str, str]:
    try:
        source = inspect.getsource(target)
    except (OSError, TypeError):
        source = f"{target.__module__}.{target.__qualname__}"
    return f"{target.__module__}.{target.__qualname__}", hashlib.sha256(source.encode()).hexdigest()


def implementation(value: Any) -> dict[str, str]:
    target = value if inspect.isfunction(value) else type(value)
    name, source_hash = _implementation(target)
    return {"name": name, "source_sha256": source_hash}


def identifier(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise ValueError(
            "Identifiers must contain 1–128 letters, digits, dots, dashes or underscores"
        )


def local_schema(schema: Any) -> None:
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key in {"$ref", "$dynamicRef"} and not str(value).startswith("#"):
                raise ValueError("Only document-local schema references are supported")
            local_schema(value)
    elif isinstance(schema, list):
        for value in schema:
            local_schema(value)


def assertion_manifest(assertion):
    if is_dataclass(assertion):
        return {
            "type": type(assertion).__name__,
            "config": asdict(assertion),
            "implementation": implementation(assertion),
        }
    if callable(getattr(assertion, "manifest", None)):
        return {
            "type": type(assertion).__name__,
            "config": assertion.manifest(),
            "implementation": implementation(assertion),
        }
    raise ValueError("Custom assertions must be dataclasses or implement manifest()")


def validate_assertions(assertions, state, handlers):
    if not assertions:
        raise ValueError("Every case requires at least one assertion")
    seen = {}
    for assertion in assertions:
        if not callable(getattr(assertion, "evaluate", None)):
            raise ValueError("Assertions must implement evaluate")
        canonical(assertion_manifest(assertion))
        if isinstance(assertion, (StateEquals, ToolPrecondition)):
            try:
                select(state, assertion.path)
            except KeyError as exc:
                raise ValueError(
                    f"Assertion path does not exist in fixture: {assertion.path}"
                ) from exc
            key = (type(assertion).__name__, getattr(assertion, "tool", None), assertion.path)
            expected = assertion.value
        elif isinstance(assertion, ToolCallCount):
            if type(assertion.count) is not int or assertion.count < 0:
                raise ValueError("Call counts must be nonnegative integers")
            if assertion.phase not in {"executed", "attempted"}:
                raise ValueError("phase must be executed or attempted")
            key, expected = ("count", assertion.tool, assertion.phase), assertion.count
        elif isinstance(assertion, ToolCallOrder):
            if any(name not in handlers for name in assertion.tools):
                raise ValueError("Order assertion targets an unknown tool")
            key, expected = ("order",), assertion.tools
        else:
            continue
        if hasattr(assertion, "tool") and assertion.tool not in handlers:
            raise ValueError(f"Assertion targets unknown tool: {assertion.tool}")
        encoded = canonical(expected)
        if key in seen and seen[key] != encoded:
            raise ValueError(f"Contradictory assertions: {key}")
        seen[key] = encoded


def prepare_case(scenario: Scenario, variant_id: str | None):
    identifier(scenario.id)
    if type(scenario.max_tool_calls) is not int or scenario.max_tool_calls < 1:
        raise ValueError("max_tool_calls must be a positive integer")
    if not isinstance(scenario.user_input, str):
        raise ValueError("user_input must be a string")
    if not callable(getattr(scenario.driver, "run", None)):
        raise ValueError("Driver must implement run")
    canonical(scenario.initial_state)
    canonical(scenario.metadata)
    for name, handler in scenario.handlers.items():
        identifier(name)
        if not callable(handler):
            raise ValueError(f"Tool handler must be callable: {name}")
    if scenario.specifications and set(scenario.specifications) != set(scenario.handlers):
        raise ValueError("When schemas are supplied, every handler requires exactly one ToolSpec")
    for spec in scenario.specifications.values():
        for schema in (spec.parameters, spec.result_schema):
            if schema is not None:
                local_schema(schema)
                Draft202012Validator.check_schema(schema)
    variants = {v.id: v for v in scenario.variants}
    if len(variants) != len(scenario.variants):
        raise ValueError("Duplicate variant IDs")
    cases = [
        (None, copy.deepcopy(dict(scenario.initial_state)), scenario.assertions, scenario.faults)
    ]
    for variant in scenario.variants:
        identifier(variant.id)
        state = copy.deepcopy(dict(scenario.initial_state))
        for path, value in variant.overrides.items():
            try:
                select(state, path)
            except KeyError as exc:
                raise ValueError(f"Invalid override path: {path}") from exc
            current = state
            parts = path.split(".")
            for part in parts[:-1]:
                current = current[part]
            current[parts[-1]] = copy.deepcopy(value)
        cases.append((variant.id, state, variant.assertions, scenario.faults + variant.faults))
    selected = None
    for case in cases:
        case_variant, state, assertions, faults = case
        validate_assertions(assertions, state, scenario.handlers)
        seen = set()
        for fault in faults:
            if fault.tool not in scenario.handlers:
                raise ValueError(f"Fault targets unknown tool: {fault.tool}")
            key = (fault.tool, fault.on_call)
            if key in seen:
                raise ValueError(f"Multiple faults target call {key}")
            seen.add(key)
            spec = scenario.specifications.get(fault.tool)
            if fault.kind == "stale_read" and spec and not spec.read_only:
                raise ValueError("stale_read requires a read-only tool")
        if variant_id == case_variant:
            selected = case
    if selected is None:
        raise ValueError(f"Unknown variant: {variant_id}")
    _, state, assertions, faults = selected
    manifest = {
        "id": scenario.id,
        "variant": variant_id,
        "state": state,
        "input": scenario.user_input,
        "assertions": [assertion_manifest(a) for a in assertions],
        "faults": [asdict(f) for f in faults],
        "max_tool_calls": scenario.max_tool_calls,
        "tools": {n: implementation(h) for n, h in scenario.handlers.items()},
        "schemas": {n: asdict(s) for n, s in scenario.specifications.items()},
        "driver": implementation(scenario.driver),
        "driver_config": scenario.driver.manifest()
        if callable(getattr(scenario.driver, "manifest", None))
        else {},
        "config": scenario.metadata,
    }
    return state, assertions, faults, digest(manifest)
