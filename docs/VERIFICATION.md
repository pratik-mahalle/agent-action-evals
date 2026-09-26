# Verify a tool's outcome

**Experimental and opt-in, included in v0.3.0a1.**

`execute_verified()` calls a tool and checks a separate operation-status tool.
It returns the observed transport result, verified business outcome, evidence,
and a next action. It uses only `ToolClient`; the evaluator's world, events,
assertions, and fault schedule remain private.

## Run the examples

```bash
agent-action-evals examples/verified_refund.py --explain
agent-action-evals examples/langgraph_verified_refund.py --repeat 3
python benchmarks/verification.py --repeat 10
```

Install `.[langgraph]` for the graph example and the three-policy benchmark.
Use `--without-langgraph` on the benchmark for a core-only comparison. Both
examples use simulated tools and scripted decisions; no credentials are needed.

The twelve cases cover normal execution, timeout before execution, lost response
after commit, false success, stale status, pending then visible, unavailable
verification, persistent pending, partial effect, wrong amount, terminal failure,
and a receipt belonging to another operation. Every configured fault must fire
in the verification examples. The delayed-visibility case has already committed
the effect; it is not a simulation of a background write committing later.

## API

```python
from agent_action_evals import (
    Operation, RecoveryPolicy, ToolContract, execute_verified,
)

contract = ToolContract(
    tool="issue_refund",
    status_tool="get_refund_status",
    effect_equals={"refund_count": 1},
    effect_matches={"order_id": "order_id", "amount_cents": "amount_cents"},
    # Set this ONLY to a guarantee provided by your service adapter.
    # The default is 0: no automatic write retry.
    idempotency_window_seconds=86400,
)

# Load a record persisted by your application before its first submission.
# Retain all three fields across retries/restarts; do not reset created_at.
operation = Operation(
    id=saved_operation["id"],
    arguments=saved_operation["arguments"],
    created_at=saved_operation["created_at"],
)

outcome = await execute_verified(
    client, contract, operation,
    RecoveryPolicy(max_attempts=2, max_checks=4, timeout_seconds=5),
    resume=True,  # Check status first after an uncertain previous invocation.
)
print(outcome.to_dict())
```

Use the default `resume=False` only for the initial invocation. It sends the write
once and then verifies, including when the response reports success. `resume=True`
checks first; a subsequent write still requires a valid service idempotency
guarantee. Without that guarantee, an unconfirmed resumed operation escalates.

The application owns persistence, authorization, operation-key uniqueness and
scope, and cumulative retry/deadline budgets across invocations. This module
does not supply a durable workflow engine or an operation database.

The status tool must have a registered `ToolSpec(read_only=True)`. The operation
key defaults to `operation_id`; it is injected separately from the business
arguments. Existing `ToolSpec`, `ToolClient`, and tool-call return values retain
their original interfaces.

## Status hook contract

A service adapter supplies a read-only tool taking the operation key. Normalize
the service's receipt to this shape:

```python
{
    "operation_id": "refund-o_123",
    "tool": "issue_refund",
    "request_sha256": request_fingerprint(
        "issue_refund", {"order_id": "o_123", "amount_cents": 500}
    ),
    "authoritative": True,
    "status": "succeeded",
    "receipt_id": "receipt-123",
    "effect": {
        "order_id": "o_123",
        "amount_cents": 500,
        "refund_count": 1,
    },
}
```

`request_fingerprint` is exported by `agent_action_evals`. The adapter must derive
the fingerprint from the service's recorded request and the effect from service
evidence. Copying the caller's desired result into these fields does not verify
anything. The fingerprint correlates requests; it is not a signature or proof
that a service is truthful.

The adapter owns the meaning of `authoritative`: use a source whose consistency
and freshness support the claim. Cached or uncertain observations must not be
labeled authoritative. A timestamp added when reading a stale cache is insufficient.
If no trustworthy status/receipt source exists, retain `unknown`.

For `not_found`, return the operation ID, tool name, authoritative flag, and
status. A request fingerprint or receipt ID is unnecessary because there is no
record. **Not found never proves that a write cannot still commit.**

For `pending`, a matching request fingerprint is required. For terminal
`succeeded`, `failed`, or `partial`, a receipt ID is also required. Success
additionally requires every declared effect postcondition. Unknown schemas,
wrong IDs, mismatched requests, and non-authoritative receipts cannot confirm
success. Expected constants and argument-matching postconditions cannot overlap.

Receipts establish the declared effect at the service's observation/commit point.
They do not provide ongoing monitoring or prevent a later actor from changing
the same business object. The example includes the order's refund count at commit;
the evaluator independently checks final state and catches duplicate effects.

## Outcome and recovery

| Outcome `status` | Meaning | `next_action` |
| --- | --- | --- |
| `confirmed_success` | A correlated authoritative receipt satisfies all postconditions | `continue` |
| `confirmed_failure` | The service reports terminal failure | `stop` |
| `contract_violation` | The receipt's reported effect contradicts the requested effect | `stop` |
| `partial` | The service reports a partial effect | `stop` |
| `pending` | The operation is still pending when the check budget is exhausted | `escalate` |
| `unknown` | Available evidence cannot establish the outcome | `escalate` |

`transport` records the latest write attempt: `returned`, `timeout`, `error`,
`unknown` (interrupted), or `not_attempted`. A returned response does not determine
the business outcome. `attempts`, `checks`, `reason`, and `evidence` explain the
decision. A terminal failure does not promise that no side effects occurred.

Within the budget, pending and uncertain observations are polled. A `not_found`
observation permits a resubmission only if the service's declared idempotency
window remains open, an attempt remains, and a later verification check remains.
Every submission reuses the same operation key and exact business arguments.
An expired key is checked but never submitted again. Partial effects and contract
violations are not automatically retried or compensated.

The service must atomically deduplicate matching requests, reject the same key
with a different payload, and honor its declared retention/scope. The wrapper
does not implement those guarantees. The simulated service implements them under
the runner's serialized tool boundary.

The timeout is cooperative, as with the trusted in-process runner. A deadline
returns `unknown`; external cancellation propagates. Either can occur after a
commit, so the application should persist the operation and resume with a status
check. An uncooperative blocking handler needs process-level isolation.

## LangGraph integration

The new example exposes one `refund_verified` tool to a real `ToolNode`. The
operation is supplied by application state, and its coroutine runs the bounded
verification policy against the injected client. The agent receives a structured
outcome and evidence. The scripted graph reports it directly. A separate Jev
decision loop has been checked live; a live LangGraph model pilot remains open.

The original LangGraph example now uses `public_tool_error()` as well. Both
`ToolTimeout` and `ToolResponseLost` map to the same public timeout/unknown result.
The internal evaluator still records which fault fired. Direct core exceptions
are retained for backward compatibility; other adapters can opt into this helper.

## Benchmark and reporting

See [recorded results](../benchmarks/results/verification.md). The benchmark compares
an explicitly weak response-trusting scripted baseline, verified Python, and
verified LangGraph on the same twelve fixtures. It measures false success claims,
duplicate effects, completion on five recoverable cases, unnecessary escalation,
tool attempts, and local latency. Repetitions do not add independent model evidence.

The evaluator judges claims against hidden committed state. Verification paths
must activate their configured faults; baseline paths may skip status calls and
therefore some status faults. Per-run activation counts are recorded.

Outcomes are available in agent output and metadata; standard report payload
redaction applies. Use `--include-payloads` only when raw receipt evidence is wanted.
No new report schema or fault kind is required for this feature.

## Live model and service validation

[The live Jev report](../benchmarks/results/live-jev.md) records 36 trials against
an authenticated Cloudflare Worker and real D1 database. Jev chose typed actions
in a bounded Python loop. Eighteen verified trials passed with no false success
claims; the direct policy made three factually false claims. The lost-response
fault hid a real successful HTTP reply after the durable write committed.

This is a custom synthetic refund ledger, not a payment provider. It demonstrates
the integration under six authored cases, not general production reliability.
The [setup guide](../benchmarks/live_service/README.md) documents the service,
blinding, fault boundaries, scoring, credential handling, and resource cleanup.
