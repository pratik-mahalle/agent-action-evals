# Wrap existing tools

`ToolBoundary` is an opt-in wrapper, added in v0.3.0a1, for existing Python functions and
LangGraph tools. It records invocations and can hide or replace an observation.
It never retries, deduplicates a write, changes business arguments, or decides
whether an agent should continue. Use a fresh boundary for each evaluation run.

The wrapper requires no simulated `World`, status endpoint, or receipt format.

## Python functions

```python
from agent_action_evals import Fault, ToolBoundary

# create_issue is your existing synchronous or async function.
boundary = ToolBoundary(
    faults=(Fault("create_issue", "response_lost_after_commit", on_call=1),)
)
tested_create_issue = boundary.wrap(create_issue)

# Register tested_create_issue wherever your agent previously used create_issue.
# Sync functions stay sync; async functions must still be awaited.
```

Decorator form and explicit names are also supported:

```python
@boundary.wrap(name="lookup_issue", read_only=True)
async def lookup_issue(title: str):
    return await existing_client.find_issues(title=title)
```

With no fault scheduled for a call, positional/keyword arguments and their object
identities pass directly to the original function. Its return value is returned
unchanged, and its exceptions propagate unchanged. The wrapper preserves callable
metadata and the signature exposed by `inspect.signature`. Bound methods and
async callable instances work; callable instances need an explicit tool name.

Run a complete example that commits to SQLite, loses the response, and checks
the row through a separate connection:

```bash
python examples/wrapped_sqlite_tool.py
```

This is a local database example. It makes no live model or external service calls.

## Existing LangGraph tools

Use the ToolNode integration for existing LangChain tools, including tools with
injected state/runtime, artifacts, custom error handling, or `Command` outputs:

```python
from agent_action_evals import Fault, ToolBoundary
from agent_action_evals.adapters.langgraph import wrap_tool_node

boundary = ToolBoundary(
    (Fault("create_issue", "response_lost_after_commit"),)
)
tools_node = wrap_tool_node(
    [create_issue_tool, lookup_issue_tool],
    boundary,
    read_only={"lookup_issue"},
    handle_tool_errors=True,
)
graph.add_node("tools", tools_node)
```

Supply your existing ToolNode options. `handle_tool_errors=True` above is an
explicit example policy that lets the agent receive an error ToolMessage;
otherwise LangGraph's normal default applies. Tool names in faults must match
the registered tool names. Unknown fault targets and invalid read-only
declarations fail during setup, before any invocation.

The adapter constructs a native ToolNode using its
[sync and async wrapper hooks](https://reference.langchain.com/python/langgraph.prebuilt/tool_node/ToolNode).
It leaves the original tool objects, schemas, validation, runtime injection,
configuration, artifact handling, and output processing with LangGraph. It passes
each request to the native executor once. Already handled error ToolMessages are
not replaced by an after-return response-loss fault.

Both `graph.invoke()` and `graph.ainvoke()` are covered by tests. This integration
was checked with LangGraph 1.2.12 / langgraph-prebuilt 1.1.0. Older installations
without both ToolNode wrapper hooks need an update; setup raises an actionable
error. The Python wrapper has no LangGraph dependency.

## Faults

Fault call numbers are one-based and counted separately per tool. Configure at
most one fault per tool/call pair. Each invocation can execute its callable at
most once.

| Kind | Behavior |
| --- | --- |
| `timeout_before` | Raise before invoking the callable; it does not run |
| `response_lost_after_commit` | Invoke normally; after it returns, discard the result and raise |
| `stale_read` | Skip an explicitly read-only callable and substitute the configured JSON value |

The first two expose exactly the same `TimeoutError` type and message:
`Tool call timed out; outcome unknown.` Their actual fault kinds remain in the
evaluator trace. Existing exceptions from the underlying callable are preserved.
Async cancellation propagates and does not trigger a retry.

The name `response_lost_after_commit` is shared with the existing simulation API.
At a real tool boundary it means **after the callable returns**, which alone does
not prove a remote commit. Use an independent service read to establish the
business effect. The wrapper does not inspect HTTP status codes or assume an SDK
return value means success. The LangGraph adapter recognizes native error
ToolMessages; arbitrary Python return values remain application-defined.

For a stale read:

```python
boundary = ToolBoundary(
    (Fault("lookup_issue", "stale_read", value={"issues": []}),)
)
tested_lookup = boundary.wrap(lookup_issue, read_only=True)
```

The Python replacement is a detached copy of the configured JSON value. At the
LangGraph boundary, it becomes a ToolMessage with the original call ID; string
values become content directly, and other JSON values become JSON text. Configure
an observation compatible with what your agent normally receives. A `read_only`
declaration is a caller contract, not enforcement on a remote service.

## Calls, repeated inputs, and observations

```python
events = boundary.events       # Detached Event snapshots, for evaluator assertions.
report = boundary.report()     # JSON-compatible metadata; no business pass/fail verdict.
```

The standalone report has `kind: "tool_boundary"`, `schema_version: "1.0"`,
injection coverage, and events. It is not a runner `RunResult`. Existing
`FaultTriggered` assertions can inspect the events. A fault that was never
reached is explicitly marked `triggered: false`.

- `tool_attempt`: unique `call_id`, per-tool `call_number`, and the originating
  LangGraph `source_call_id` when available.
- `tool_executed`: callable returned, raised, or was cancelled, plus duration.
  No remote state transition is inferred.
- `tool_fault`: the configured fault actually fired.
- `tool_error`: the original exception type, without its message.
- `tool_observation`: the returned value or exception visible at this boundary.
  LangGraph may subsequently format an exception according to its error policy.

Attempts with identical bounded JSON-compatible positional and keyword inputs
have a `same_input_as` link to the previous matching call ID. This makes retries
visible without assuming that every repeated request is a retry. Positional and
keyword forms are not normalized, and omitted defaults differ from explicit
values. Opaque, cyclic, or truncated inputs get no repeated-input link.

Concurrent calls remain concurrent. A short metadata lock assigns call numbers;
no lock is held while a tool runs or is awaited. Fault targeting follows invocation
arrival order, which can differ between concurrent runs. Start a fresh boundary
for each run, and use stable ordering when a fault must target a particular call.

## Payloads and limits

Arguments and results are not retained by default. For scrubbed test data, opt in
with `ToolBoundary(capture_payloads=True)`, then export with
`boundary.report(include_payloads=True)`. The normal export still omits payloads.
Do not put secrets in registered tool names or call IDs; these are trace metadata.

Capture uses bounded snapshots of built-in containers and primitives. Unknown
objects are recorded as `<opaque>` without invoking `repr`, serializers, or copy
hooks. Snapshots may be truncated; they are debugging evidence, not faithful
serialization of every SDK object. Original inputs and results are unaffected.
No runtime state, configuration, environment variables, or exception messages
are automatically collected.

This is a trusted in-process boundary, not a sandbox. Calls that bypass the
wrapped callable are not observed. SDK-internal retries appear as a single
invocation; HTTP-level instrumentation is a separate concern. Traces accumulate
in memory for the life of the boundary.

Generator functions and async generators need a stream-aware boundary and are
rejected at wrapping time. A regular function returning a deferred awaitable or
iterator is observed at that function's return; wrap an async function that
awaits completion if the fault must occur after the deferred work.

An external-state oracle, reusable real-service packs, and automatic runner/CLI
integration for these standalone traces are subsequent milestones. Business
assertions must check the real effects and the agent's claim independently.
