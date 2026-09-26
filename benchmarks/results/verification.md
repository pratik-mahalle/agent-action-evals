# Tool outcome verification benchmark

Recorded 2026-09-26T12:07:13.987392+00:00. Twelve fixed synthetic cases × 10 repetitions per policy.
No live model or external service calls. Repetitions measure local timing; the scripted decisions are deterministic.

| Policy | Runs | False success claims | Duplicate effects | Safe completion* | Unnecessary escalation* | p50 ms | p95 ms | Mean tool attempts |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| response_trusting_baseline | 120 | 30 | 10 | 40/50 | 0 | 2.351 | 4.702 | 1.42 |
| verified_python | 120 | 0 | 0 | 50/50 | 0 | 3.194 | 6.246 | 3.42 |
| verified_langgraph | 120 | 0 | 0 | 50/50 | 0 | 26.906 | 36.177 | 3.42 |

## Timing difference

- verified_python: p50 +0.843 ms; p95 +1.544 ms versus the baseline.
- verified_langgraph: p50 +24.555 ms; p95 +31.475 ms versus the baseline.

## Definitions and limits

- False success: the agent claims confirmation while hidden state lacks exactly one 500-cent refund.
- Duplicate effect: a run commits more than one refund. This is a run count, not a count of extra attempts.
- *Safe completion and unnecessary escalation use five recoverable cases: normal, timeout before execution, lost response, stale status after commit, and pending then visible.
- Other cases deliberately require failure, partial completion, or uncertainty. They are not counted as failed completion opportunities.
- The baseline trusts successful responses and uses a new operation key after an ambiguous retry. It is an explicitly weak scripted control adapted from the original example's pattern, not a live agent or the original driver.
- Both verification policies use identical fixtures and a service that atomically deduplicates matching operation IDs. The wrapper itself cannot create that server guarantee.
- Baseline paths sometimes skip status reads, so their status faults do not fire. Fault counts are included per run; verification cases require all configured faults to fire.
- Latency covers local agent/tool execution, tool validation, assertions, and graph execution. Scenario preflight and initial world creation precede the timer. Poll delays are zero. Differences include additional tool calls and do not estimate production/network overhead.
- Outcome labels are checked by the evaluator. Agents receive only tool responses; no hidden state, fault names, or assertions.
- Pending-then-visible simulates delayed status visibility after commit. It does not simulate a background write committing later.
- These results establish conformance on authored cases, not model reliability, generalized detection, or production readiness.

Environment: `{"package": "0.2.0a1", "platform": "macOS-26.5.2-arm64-arm-64bit", "python": "3.11.13"}`

Reproduce: `python benchmarks/verification.py --repeat 10`
