"""Observe existing callables and inject faults without managing their recovery."""

from __future__ import annotations

import asyncio
import copy
import functools
import hashlib
import inspect
import json
import math
import threading
import time
from collections import Counter
from typing import Any, Callable

from .core import Event, Fault

_TIMEOUT = "Tool call timed out; outcome unknown."


def _snapshot(value):
    """Bounded JSON snapshot; never call user repr, serializers, or deepcopy hooks."""
    remaining = 2000
    exact = True
    ancestors = set()

    def visit(item, depth=0):
        nonlocal remaining, exact
        remaining -= 1
        if remaining < 0 or depth > 12:
            exact = False
            return "<truncated>"
        if type(item) is int and item.bit_length() > 4096:
            exact = False
            return "<large integer>"
        if item is None or type(item) in (bool, int):
            return item
        if type(item) is float and math.isfinite(item):
            return item
        if type(item) is str:
            if len(item) > 8192:
                exact = False
                return item[:8192] + "<truncated>"
            return item
        if type(item) not in (dict, list, tuple):
            exact = False
            return "<opaque>"
        if id(item) in ancestors:
            exact = False
            return "<cycle>"
        ancestors.add(id(item))
        try:
            if len(item) > 100:
                exact = False
                return "<truncated container>"
            if type(item) is dict:
                if any(type(key) is not str for key in item):
                    exact = False
                    return "<non-string keys>"
                return {key: visit(val, depth + 1) for key, val in item.items()}
            if type(item) is tuple:
                # A tuple and list must not be inferred to be the same input.
                exact = False
            return [visit(val, depth + 1) for val in item]
        finally:
            ancestors.remove(id(item))

    try:
        return visit(value), exact
    except Exception:
        # For example, another thread mutating a container while it is observed.
        return "<unavailable>", False


class ToolBoundary:
    """One trace and fault schedule per run; sync/async callables retain their behavior.

    Payload capture is opt-in. An invocation is never retried by this class.
    ``response_lost_after_commit`` is the existing fault name; here it means
    after the callable returns, NOT proof of a remote commit.
    """

    def __init__(self, faults: tuple[Fault, ...] = (), *, capture_payloads: bool = False):
        self._faults = copy.deepcopy(tuple(faults))
        if any(not isinstance(fault, Fault) for fault in self._faults):
            raise TypeError("faults must contain Fault instances")
        targets = [(f.tool, f.on_call) for f in self._faults]
        if len(set(targets)) != len(targets):
            raise ValueError("Only one fault may target a tool call")
        self._schedule = dict(zip(targets, self._faults))
        self._capture_payloads = capture_payloads
        self._events: list[Event] = []
        self._counts: Counter = Counter()
        self._previous: dict[tuple[str, str], int] = {}
        self._lock = threading.Lock()
        self._calls = 0

    @property
    def events(self) -> tuple[Event, ...]:
        """Detached trace snapshot. Payloads exist only when explicitly captured."""
        with self._lock:
            return tuple(copy.deepcopy(self._events))

    def report(self, *, include_payloads: bool = False) -> dict[str, Any]:
        """JSON-compatible trace and injection coverage, without a business verdict."""
        events = self.events
        return {
            "schema_version": "1.0",
            "kind": "tool_boundary",
            "payloads_captured": self._capture_payloads,
            "faults": [
                {
                    "tool": f.tool,
                    "kind": f.kind,
                    "on_call": f.on_call,
                    "triggered": any(
                        e.kind == "tool_fault"
                        and e.tool == f.tool
                        and e.details["call_number"] == f.on_call
                        for e in events
                    ),
                }
                for f in self._faults
            ],
            "events": [
                {
                    "seq": e.seq,
                    "kind": e.kind,
                    "tool": e.tool,
                    "details": {
                        k: v
                        for k, v in e.details.items()
                        if include_payloads or k not in {"arguments", "result"}
                    },
                }
                for e in events
            ],
        }

    def _validate(self, name, read_only):
        if not isinstance(name, str) or not name:
            raise ValueError("A tool needs a nonempty name")
        if not read_only and any(f.tool == name and f.kind == "stale_read" for f in self._faults):
            raise ValueError("stale_read requires an explicitly read-only tool")

    def wrap(self, function: Callable | None = None, *, name: str | None = None, read_only=False):
        """Wrap a sync or async function, or use as a decorator with options."""
        if function is None:
            return lambda fn: self.wrap(fn, name=name, read_only=read_only)
        if not callable(function):
            raise TypeError("Expected a callable")
        target = (
            function if inspect.isroutine(function) else getattr(function, "__call__", function)
        )
        if any(
            inspect.isgeneratorfunction(fn) or inspect.isasyncgenfunction(fn)
            for fn in (function, target)
        ):
            raise TypeError("Streaming generators require a separate stream boundary")
        tool_name = name or getattr(function, "__name__", None)
        self._validate(tool_name, read_only)
        if inspect.iscoroutinefunction(function) or inspect.iscoroutinefunction(target):

            @functools.wraps(function)
            async def invoke(*args, **kwargs):
                return await self._acall(tool_name, args, kwargs, lambda: function(*args, **kwargs))

        else:

            @functools.wraps(function)
            def invoke(*args, **kwargs):
                return self._call(tool_name, args, kwargs, lambda: function(*args, **kwargs))

        return invoke

    def _append(self, kind, name, details):
        with self._lock:
            self._events.append(Event(len(self._events) + 1, kind, name, details))

    def _start(self, name, args, kwargs, source_call_id=None):
        arguments, exact = _snapshot({"args": list(args), "kwargs": kwargs})
        fingerprint = None
        if exact:
            try:
                encoded = json.dumps(arguments, sort_keys=True, allow_nan=False).encode()
                fingerprint = hashlib.sha256(encoded).hexdigest()
            except (ValueError, OverflowError):
                pass
        with self._lock:
            self._counts[name] += 1
            self._calls += 1
            number = self._counts[name]
            context = {"call_id": self._calls, "call_number": number}
            details = dict(context)
            if source_call_id is not None:
                details["source_call_id"] = source_call_id
            if fingerprint is not None:
                key = (name, fingerprint)
                if key in self._previous:
                    details["same_input_as"] = self._previous[key]
                self._previous[key] = self._calls
            if self._capture_payloads:
                details["arguments"] = arguments
            self._events.append(Event(len(self._events) + 1, "tool_attempt", name, details))
        return context, self._schedule.get((name, number))

    def _record(self, kind, name, context, **details):
        self._append(kind, name, {**context, **details})

    def _observe(self, name, context, result, view):
        details = {"status": "returned"}
        if self._capture_payloads:
            details["result"] = _snapshot(view(result) if view else result)[0]
        self._record("tool_observation", name, context, **details)

    def _timeout(self, name, context, fault):
        self._record("tool_fault", name, context, fault_kind=fault.kind)
        self._record("tool_observation", name, context, status="error", error_type="TimeoutError")
        # Exactly the same public exception for before-send and after-return loss.
        raise TimeoutError(_TIMEOUT)

    def _before(self, name, context, fault, stale, view):
        if fault and fault.kind == "timeout_before":
            self._timeout(name, context, fault)
        if fault and fault.kind == "stale_read":
            result = copy.deepcopy(fault.value)
            if stale:
                result = stale(result)
            self._record("tool_fault", name, context, fault_kind=fault.kind)
            self._observe(name, context, result, view)
            return True, result
        return False, None

    def _raised(self, name, context, started, exc):
        status = "cancelled" if isinstance(exc, asyncio.CancelledError) else "error"
        self._record(
            "tool_executed",
            name,
            context,
            status=status,
            duration_ms=(time.perf_counter() - started) * 1000,
        )
        self._record("tool_error", name, context, error_type=type(exc).__name__)
        self._record(
            "tool_observation", name, context, status=status, error_type=type(exc).__name__
        )

    def _returned(self, name, context, started, result, fault, view, is_error):
        failed = is_error(result) if is_error else False
        self._record(
            "tool_executed",
            name,
            context,
            status="error_result" if failed else "returned",
            duration_ms=(time.perf_counter() - started) * 1000,
        )
        if fault and fault.kind == "response_lost_after_commit" and not failed:
            self._timeout(name, context, fault)
        self._observe(name, context, result, view)
        return result

    def _call(
        self,
        name,
        args,
        kwargs,
        execute,
        *,
        source_call_id=None,
        stale=None,
        view=None,
        is_error=None,
    ):
        context, fault = self._start(name, args, kwargs, source_call_id)
        short, result = self._before(name, context, fault, stale, view)
        if short:
            return result
        started = time.perf_counter()
        try:
            result = execute()
        except BaseException as exc:
            self._raised(name, context, started, exc)
            raise
        return self._returned(name, context, started, result, fault, view, is_error)

    async def _acall(
        self,
        name,
        args,
        kwargs,
        execute,
        *,
        source_call_id=None,
        stale=None,
        view=None,
        is_error=None,
    ):
        context, fault = self._start(name, args, kwargs, source_call_id)
        short, result = self._before(name, context, fault, stale, view)
        if short:
            return result
        started = time.perf_counter()
        try:
            result = await execute()
        except BaseException as exc:
            self._raised(name, context, started, exc)
            raise
        return self._returned(name, context, started, result, fault, view, is_error)
