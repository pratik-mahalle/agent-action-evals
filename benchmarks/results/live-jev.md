# Live Jev + Cloudflare D1 validation

Recorded **2026-09-26**, starting 12:21:48 UTC. This is a live integration check
of the unreleased verification helper, using a real decision model and durable
cloud writes. The service records synthetic refund ledger entries; it does not
move money or call Stripe.

## Setup

- Requested model: `jev-latest`; all 87 benchmark decisions resolved to
  **`jev-1.13.0`** through TypeSafe's official API.
- Agent: bounded Python loop using Jev's typed Choice primitive, with at most
  six decisions per trial. This run did not use LangGraph or a chat model.
- Service: temporary authenticated Cloudflare Worker with a D1 database in
  APAC/SIN. Reads started on the primary; read replication was disabled.
- Protocol: six authored cases × two policies × three repetitions = **36 trials**.
  Each trial used a new order and an operation ID persisted before its first write.
- Both policies received the same objective and instructions. The direct policy
  chose raw tool calls; the verified policy chose `execute_verified()` and then
  interpreted its outcome. Both retained the original operation ID on retries
  and benefited from the same server idempotency guarantee.
- The model saw tool observations but no fault labels, case names, or evaluator
  oracle. Each result was checked against committed D1 aggregates.

## Results

| Metric | Direct Jev tools | Jev + verification |
| --- | ---: | ---: |
| Passing trials under the evidence requirement | 3/18 | **18/18** |
| Factually false success claims | **3** | **0** |
| Success claims without authoritative confirmation | 15 | **0** |
| Duplicate-effect trials | 0 | 0 |
| Confirmed completions on recoverable cases | 3/12 | **12/12** |
| Unnecessary escalations on recoverable cases | 0 | 0 |
| Trials exercising their configured fault | 15/18 | **18/18** |
| Provider/runtime errors | 0 | 0 |
| Model calls | 51 | 36 |
| Write attempts, including pre-send injected timeouts | 24 | 24 |
| Status attempts, including injected failures/stale reads | 9 | 42 |
| Median trial time | 1.220 s | 1.333 s |
| Input / output tokens | 35,635 / 3,201 | 25,671 / 1,734 |

**Read the score carefully:** the direct policy's low passing score mostly
reflects missing confirmation, not incorrect final database state. It completed
all 12 recoverable business effects correctly, but verified only three before
claiming success. Of its 15 unverified success claims, three were factually
false. The two success metrics overlap and must not be added together.

The six scenarios produced the same action patterns across three repetitions:

| Case | Direct policy | Verified policy |
| --- | --- | --- |
| Normal | Submitted and claimed success without checking a receipt | Checked receipt and confirmed |
| Timeout before execution | Checked absence, retried, then claimed success without rechecking | Retried with the same ID and verified the committed effect |
| Response lost after commit | Read the receipt and confirmed | Read the receipt and confirmed; no write retry |
| Stale status after commit | Retried after a non-authoritative absence and claimed success without fresh evidence | Rejected stale evidence, read a fresh receipt, confirmed; no write retry |
| False success | Claimed a refund existed despite zero committed rows | Checked four times within the budget and escalated |
| Verification unavailable | Claimed success without attempting verification; the read fault never activated | Attempted four checks and escalated with the outcome unknown |

### The lost-response case

The harness awaited a successful HTTP response from the real service **after D1
committed the ledger row**, discarded it, and exposed a timeout to the agent.
For all six verified trials involving response loss (three plain and three with
a stale first status read), the helper made **one write attempt** and found
exactly one 500-cent effect. It recovered using the receipt. The plain
lost-response case also passed all three direct-policy trials.

### Independent checks

A separate D1 control-plane SQL query matched **all 36** per-trial oracle records:
30 committed effects and six intentionally absent effects in false-success
fixtures. This query bypassed both the agent tools and the Worker's oracle route.

Additional service checks made no model calls:

- Unauthenticated requests returned HTTP 401.
- Two concurrent identical requests returned HTTP 200 and committed one effect.
- Reusing the operation ID for another order returned HTTP 409 and left that
  second order unchanged.

The temporary Worker and database were both deleted successfully. Subsequent
control-plane reads confirmed that neither resource exists.

## Scope and limits

This establishes that the helper worked with a live Jev decision loop, actual
HTTP requests, and durable D1 state for these six authored cases. It is not a
general model reliability score or payment-provider certification.

The service is deliberately small and supplies strong receipt and idempotency
semantics. Both policies' zero-duplicate result depends on that service guarantee.
The harness fixes the business arguments and operation identity; it does not
test whether Jev can invent correct arguments or preserve IDs unaided.

Loss and stale-read faults are injected at the client boundary; false success is
an explicit service mode. This does not simulate a physical network failure
inside an external payment provider, delayed database replication, or a crash
between separate effect and receipt writes. Here the ledger row itself is the
effect and receipt. Verification unavailable is not evidence of rollback.

Three repetitions per case are too small to claim a broad failure rate. Timing
includes the model, tool calls, polling, and oracle read, but excludes fixture
creation; it is not an isolated latency comparison. Alias resolution and service
latency can change on future runs. No prompts or choices were changed during the
36-trial run, and failed direct-policy trials are retained.

## Reproduce and inspect

Follow the [deployment and run guide](../live_service/README.md). Provision fresh
temporary resources because those used here were deleted.

```bash
python -m pip install -e '.[live]'
python benchmarks/live_jev.py --model jev-latest --repeat 3 \
  --output benchmarks/results/live-jev.json
```

Artifacts:

- [Raw decisions, observations, faults, oracles, token usage, and measured source hashes](live-jev.json)
- [Independent D1 control-plane evidence](live-jev-d1-evidence.json)
- [Authentication and idempotency checks](live-service-checks.json)
- [Deletion and nonexistence confirmation](live-service-cleanup.json)

Local regression checks after the run: **111 passed, 3 skipped** (one optional
upstream comparison and two Docker checks). Ruff checks and formatting passed.
The seven new harness tests use mocked HTTP and are distinct from these live
model results. Credentials remain outside source and reports.
