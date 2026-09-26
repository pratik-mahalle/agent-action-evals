import asyncio
import inspect
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent_action_evals import Fault, FaultTriggered, ToolBoundary


def test_sync_pass_through_preserves_signature_identity_and_input_mutations():
    class Opaque:
        def __repr__(self):
            raise AssertionError("Must not inspect opaque objects")

        def __deepcopy__(self, memo):
            raise AssertionError("Must not copy opaque objects")

    result, item = Opaque(), Opaque()
    seen = []

    def original(value, /, *, flag: bool = False):
        """Original documentation."""
        assert value is item and flag is True
        seen.append(value)
        return result

    boundary = ToolBoundary(capture_payloads=True)
    wrapped = boundary.wrap(original)
    assert inspect.signature(wrapped) == inspect.signature(original)
    assert wrapped.__name__ == original.__name__
    assert wrapped.__doc__ == original.__doc__
    assert inspect.unwrap(wrapped) is original
    assert wrapped(item, flag=True) is result
    assert seen == [item]
    json.dumps(boundary.report(include_payloads=True))


@pytest.mark.parametrize("asynchronous", [False, True])
def test_before_and_after_timeouts_are_indistinguishable_but_effects_differ(asynchronous):
    traces, errors, effects = [], [], []
    for kind in ("timeout_before", "response_lost_after_commit"):
        committed = []

        def write(value):
            committed.append(value)
            return {"created": value}

        async def awrite(value):
            await asyncio.sleep(0)
            return write(value)

        boundary = ToolBoundary((Fault("write", kind),), capture_payloads=True)
        wrapped = boundary.wrap(awrite if asynchronous else write, name="write")
        assert inspect.iscoroutinefunction(wrapped) == asynchronous
        with pytest.raises(TimeoutError) as caught:
            asyncio.run(wrapped("one")) if asynchronous else wrapped("one")
        errors.append((type(caught.value), caught.value.args))
        effects.append(list(committed))
        traces.append(boundary.events)
        assert boundary.report()["faults"][0]["triggered"]
        assert FaultTriggered("write", kind).evaluate(None, boundary.events) is None
        assert [e.details for e in boundary.events if e.kind == "tool_observation"] == [
            {"call_id": 1, "call_number": 1, "status": "error", "error_type": "TimeoutError"}
        ]
        # The wrapper never retries. A second call is an explicit caller decision.
        assert (asyncio.run(wrapped("one")) if asynchronous else wrapped("one")) == {
            "created": "one"
        }
        attempts = [e for e in boundary.events if e.kind == "tool_attempt"]
        assert attempts[1].details["same_input_as"] == attempts[0].details["call_id"]
    assert errors[0] == errors[1]
    assert effects == [[], ["one"]]
    assert not any(e.kind == "tool_executed" for e in traces[0])
    assert any(e.kind == "tool_executed" for e in traces[1])


def test_stale_reads_need_explicit_read_only_and_do_not_touch_the_tool():
    fault = Fault("read", "stale_read", value={"items": []})
    boundary = ToolBoundary((fault,))
    with pytest.raises(ValueError, match="read-only"):
        boundary.wrap(lambda: None, name="read")
    calls = []

    @boundary.wrap(name="read", read_only=True)
    def read():
        calls.append(1)
        return {"items": ["fresh"]}

    fault.value["items"].append("changed after configuration")
    stale = read()
    assert stale == {"items": []} and calls == []
    stale["items"].append("changed by caller")
    assert read() == {"items": ["fresh"]}
    assert calls == [1]


def test_original_exception_preserved_and_response_loss_does_not_mask_failure():
    failure = ValueError("a-secret-in-provider-error")
    boundary = ToolBoundary((Fault("write", "response_lost_after_commit"),))

    @boundary.wrap
    def write():
        raise failure

    with pytest.raises(ValueError) as caught:
        write()
    assert caught.value is failure
    assert not boundary.report()["faults"][0]["triggered"]
    assert "a-secret" not in json.dumps(boundary.report())


def test_payloads_opt_in_snapshot_mutation_and_exports_redact():
    secret = "secret-argument-value"
    boundary = ToolBoundary(capture_payloads=True)
    original = {"value": secret}
    output = {"items": [secret]}

    @boundary.wrap
    def tool(value):
        assert value is original
        value["value"] = "mutated by tool"
        return output

    assert tool(original) is output
    output["items"].clear()
    report = boundary.report(include_payloads=True)
    assert report["events"][0]["details"]["arguments"]["args"] == [{"value": secret}]
    assert report["events"][-1]["details"]["result"] == {"items": [secret]}
    assert secret not in json.dumps(boundary.report())
    detached = boundary.events
    detached[0].details.clear()
    assert boundary.events[0].details
    private = ToolBoundary()
    private.wrap(lambda value: value, name="tool")(secret)
    assert secret not in json.dumps(private.report(include_payloads=True))


def test_async_cancellation_propagates_after_effect_without_retry():
    async def run():
        entered = asyncio.Event()
        effects = []
        boundary = ToolBoundary()

        @boundary.wrap
        async def write():
            effects.append("committed")
            entered.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(write())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert effects == ["committed"]
        assert [e.details["status"] for e in boundary.events if e.kind == "tool_executed"] == [
            "cancelled"
        ]

    asyncio.run(run())


def test_async_calls_are_not_serialized_and_completion_order_keeps_call_ids():
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        boundary = ToolBoundary((Fault("tool", "response_lost_after_commit", on_call=2),))

        @boundary.wrap
        async def tool(value):
            if value == "slow":
                entered.set()
                await release.wait()
            return value

        slow = asyncio.create_task(tool("slow"))
        await entered.wait()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(tool("fast"), 1)
        release.set()
        assert await slow == "slow"
        returned = [e.details["call_number"] for e in boundary.events if e.kind == "tool_executed"]
        assert returned == [2, 1]
        assert [e.seq for e in boundary.events] == list(range(1, len(boundary.events) + 1))

    asyncio.run(run())


def test_threads_keep_unique_counts_without_serializing_tool_execution():
    gate = threading.Barrier(4, timeout=3)
    boundary = ToolBoundary()

    @boundary.wrap
    def tool(value):
        gate.wait()
        return value

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(tool, range(4))) == list(range(4))
    attempts = [e.details["call_number"] for e in boundary.events if e.kind == "tool_attempt"]
    assert attempts == [1, 2, 3, 4]


def test_configuration_errors_and_unused_faults():
    with pytest.raises(ValueError, match="one fault"):
        ToolBoundary((Fault("tool", "timeout_before"), Fault("tool", "stale_read")))
    boundary = ToolBoundary((Fault("tool", "timeout_before", on_call=2),))
    assert boundary.wrap(lambda: 42, name="tool")() == 42
    assert not boundary.report()["faults"][0]["triggered"]

    def streaming():
        yield 1

    with pytest.raises(TypeError, match="Streaming"):
        boundary.wrap(streaming)


def test_bound_method_and_async_callable_object():
    class ExistingTool:
        def __init__(self):
            self.value = 0

        def increment(self, n=1):
            self.value += n
            return self

        async def __call__(self, n=1):
            return self.increment(n)

    original = ExistingTool()
    boundary = ToolBoundary()
    assert boundary.wrap(original.increment)(2) is original
    assert asyncio.run(boundary.wrap(original, name="increment_async")(3)) is original
    assert original.value == 5
