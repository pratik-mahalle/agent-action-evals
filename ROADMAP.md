# Production and benchmark plan

**Status:** Work plan updated 26 September 2026

**Target:** A public Python package that external teams can run safely and repeatably in CI against their own agents.

The first-alpha core suite passed the hosted Python/OS matrix, including the two real Docker checks on Linux ([CI evidence](https://github.com/pratik-mahalle/agent-action-evals/actions/runs/36164942542)). The ten-case LangGraph example passes 100/100 offline runs. A recorded local comparison with the pinned Failproof SDK detects the same three seeded failures in both implementations, with nine safe controls and no false alarms. The v0.3.0a1 suite passes 130 local tests; the optional upstream comparison and two Docker checks are skipped in this environment. The original refund pack passes 56/56 offline runs across Python and LangGraph. The verification benchmark passes 120/120 authored scenarios in each verified adapter. A separate live Jev/custom-D1 integration check passed 18/18 verified trials. Existing-service adapters, hosted comparison integration, and external-team adoption remain unverified.

## Scope decision after the Failproof comparison

Focus on reusable fault-injection fixtures, state assertions, and adapters into existing evaluation workflows. The [comparison](docs/FAILPROOF_COMPARISON.md) demonstrated equivalent detection on three synthetic families. Reduced simulation setup is the next hypothesis to validate with two external teams. Broader platform work should wait for that evidence.

## Implementation status

### v0.3.0a1: test existing tools

`ToolBoundary.wrap()` now instruments existing sync/async Python callables without
requiring a `World` or receipt contract. `wrap_tool_node()` uses native LangGraph
hooks to retain tool schemas, runtime injection, artifacts, and outputs. Both
record attempts, repeated inputs, observations, and fault coverage; neither
implements agent recovery. The [guide](docs/TOOL_BOUNDARY.md) covers the interface
and limits. A local SQLite example checks an actual commit after response loss.

Next: add an independent external-state observer and the first real GitHub Issues
scenario pack, connect standalone boundary traces to pytest/CLI evaluation, then
validate a developer's existing agent. The live Jev/D1 result is an integration
smoke test; it does not complete this real-tool adoption milestone.

### v0.3.0a1: optional operation verification

An opt-in `execute_verified()` layer now correlates authoritative status receipts
to persistent operation IDs and request fingerprints, checks declared business
effects, and returns structured outcomes. Recovery is bounded and write retries
require an explicit service idempotency guarantee. The original LangGraph error
handler now exposes identical observations for both ambiguous timeout cases.

Twelve synthetic cases run through both Python and a real LangGraph ToolNode.
The [verification benchmark](benchmarks/results/verification.md) compares those
policies with a response-trusting scripted baseline. The
[guide](docs/VERIFICATION.md) documents the receipt contract, persistence duties,
retry windows, cancellation, and limits. A live Jev/custom-D1 integration check
is recorded separately. Existing-service adapters, durable application integration,
and production-team validation remain open.

### First alpha

The first adoption milestone is implemented: a reusable 14-case refund pack,
custom tool/state bindings, bounded automatic fault-case generation, explicit
injection coverage, and readable failure reports with rerun commands. Python and
offline LangGraph examples share the pack. See [the guide](docs/SCENARIO_PACKS.md).
Booking/account-access packs, pytest integration, baseline comparisons, and
external-team/live-model validation remain future work.

| Area | Implemented and checked | Remaining gate |
| --- | --- | --- |
| P0.1 isolation | Docker adapter and real Linux CI checks for blocked network/root writes, tool execution, and timeout cleanup | Broader adversarial review and production-team validation |
| P0.2 integration | LangGraph adapter, ten shared offline/live cases, real ToolNode and unsafe-retry tests, configurable Anthropic example | Configure model credentials and benchmark a team's actual agent |
| P0.3 execution | Serialized tool effects, committed-state capture on errors/cancellation, sealed worlds, subprocess cleanup; Docker timeout cleanup verified in CI | Host Python remains trusted/cooperative |
| P0.4 validation | Preflight IDs, cases, paths, contradictions, schemas, fault targets; rejected calls remain failures | Broader pilot feedback on custom domains |
| P0.5 reproducibility | Scenario/configuration/source hashes, versions, seed/deadline metadata, immutable Docker image ID | Custom driver configuration must be declared; external service state is not captured |
| P0.6 reporting | Versioned JSON envelope, JSONL checkpoints, JUnit classification, default payload redaction | Backward compatibility across future releases |
| P0.7 attribution | Direct violating events and transitions are labeled; uncertain attribution remains explicit | Validate explanations on real production incidents |
| P0.8 release | Public repository, private reporting, green hosted CI matrix, dependency lockfile, source/wheel builds, clean-wheel example, contributor/reporting docs; v0.2.0a1 alpha release prepared | Validate external-team and live-model pilots before a stable release |
| B1 evaluator | 160 labeled executions, 40 seeded unsafe cases detected, 0 false alarms | Eight parameterized templates provide conformance evidence, not broad generalization |
| B2 live reliability | Shared live example; offline graph passes 100/100; live Jev/custom-D1 check passes 18/18 verified trials | A team's existing agent, native service adapters, held-out real cases |
| B3 comparison | Pinned Failproof SDK component comparison: both detect 3/3 failures and pass 9/9 safe controls | Real-team setup study; hosted integration and other comparators remain untested |
| B4 overhead | 100/1,000/10,000-run measurements at two fixture sizes | External-team usability pilots |

Measured results and caveats are in [the synthetic benchmark](benchmarks/results/local.md) and [the offline LangGraph integration results](benchmarks/results/langgraph-offline.md).

## Release sequence

### P0 — make the test result trustworthy

| ID | Task | Completion check |
| --- | --- | --- |
| P0.1 | Enforce the tool boundary in a subprocess sandbox. Remove production credentials and block outbound network access in the test process. | A deliberately bypassing network/database call fails and is reported; a registered tool call still succeeds. |
| P0.2 | Integrate one real model-backed agent from a production team through an adapter. Start with the framework that team actually uses. | Their existing agent runs ten scenarios without changing its business logic; all effectful tools route through the test port. |
| P0.3 | Harden run isolation and cancellation. Handle concurrent calls, timeouts during a tool effect, handler exceptions after a commit, and cleanup. | Repeated and parallel runs cannot share state; a timed-out run cannot mutate a later run; partial effects remain visible to assertions. |
| P0.4 | Validate scenarios before model invocation. Check unique IDs, override paths, fault targets, assertion types, tool arguments/results, and contradictory rules. | Invalid cases fail with an actionable error and make zero model calls. |
| P0.5 | Make runs reproducible and auditable. Record scenario hash, package version, model/provider configuration, tool schema versions, fault schedule, and relevant seeds. | A report identifies the exact scenario and configuration used; framework-controlled faults replay at the same step. Model output is explicitly treated as nondeterministic. |
| P0.6 | Stabilize the event and report schema. Version JSON/JSONL output, include before/after effect IDs, redact sensitive fields in every export path, and document the schema. | A consumer can parse reports from two package versions; secrets placed in test arguments, results, exceptions, and state do not appear in default artifacts. |
| P0.7 | Fix first-failure explanations. Distinguish a directly observed violating action from an uncertain cause. | On a labeled failure corpus, reports point to the first offending physical effect when one exists and avoid claiming a cause when it cannot be established. |
| P0.8 | Add release engineering. Run CI on Python 3.11–3.14, build/install the wheel, check CLI exit codes, publish API docs, and add contribution and security reporting instructions. | A clean machine can install the package and run the examples; all supported environments pass CI. |

### P1 — make the framework useful to teams

| ID | Task | Completion check |
| --- | --- | --- |
| P1.1 | Add scenario authoring helpers for paired cases: `should_act`, `should_ask`, and `should_stop`, plus state and event assertions. | A team can turn a real incident into a base case and one paired variant without copying a full scenario. |
| P1.2 | Add scripted multi-turn users and explicit approval events. | Tests cover an agent asking for missing information and then acting only after the answer or approval arrives. |
| P1.3 | Support a second integration path, chosen from a partner team's stack, such as MCP or a language-neutral subprocess protocol. | Two distinct agent implementations run the same domain scenarios with the same verdicts. |
| P1.4 | Improve developer workflow: pytest integration, filtering, rerunning failures, concise diffs, and examples for common domains. | A failed CI job gives the case ID, violating event, and a local reproduction command. |
| P1.5 | Measure overhead and optimize state handling. The current world rewrites one JSON document on each `set`. | Publish p50/p95 run overhead, peak memory, and artifact size for fixed fixture sizes; set an optimization target from measured pilot needs. |

## Benchmark program

Benchmark the **test framework** and the **agents being tested** separately. A fast runner does not imply a useful evaluator, and a high agent score does not prove the evaluator is correct.

### B1 — evaluator correctness (no model variance)

Build a labeled corpus with at least 40 paired cases across four categories: authorized action, missing authorization or identity, ambiguous target, and uncertain tool outcome. Include seeded unsafe implementations such as duplicate retry, wrong entity, acting before approval, and claiming success after a failed effect. Include safe implementations that should pass.

Measure:

- Detection recall for known unsafe effects and false-positive rate on safe runs.
- Accuracy of the reported first violating event, judged against a human-labeled event ID.
- Coverage: which failure types are expressible with the public scenario API.
- Time to write each scenario, including custom tool handlers.

**Gate:** every critical unsafe effect in the seeded corpus is detected, no safe reference case fails without an explained rule, and first-event accuracy is reported rather than assumed. Keep the corpus and expected labels versioned.

### B2 — real-agent reliability

Run one or more real production-style agents on a held-out set of paired scenarios. Use at least 10 independent runs per case initially, increasing repetitions where uncertainty remains. Reset world state between runs and record model version, settings, prompts, tool schemas, cost, and latency. Report separate scores for the original and changed-fact variants.

Primary metrics: task success, unsafe-action rate, unnecessary-abstention rate, and duplicate-effect rate. Secondary metrics: tool calls, latency, model cost, and recovery after injected faults. Show counts and uncertainty intervals; do not report only an average score or a best-of-many pass rate.

### B3 — comparison with existing tools

Use the same agent, prompts, tool schemas, fixtures, and labeled failures where feasible. Compare against [Promptfoo trace assertions](https://www.promptfoo.dev/docs/tracing/) and [Agent Crash Test effect contracts](https://github.com/pavloparaschakis/agent-crash-test). Report both detection and setup work. Label cases that a comparator does not support directly instead of scoring them as misses.

The differentiation question is whether paired action-boundary cases catch meaningful failures with less authoring work or clearer evidence than existing approaches. Agent Crash Test already covers effect verification and ambiguous commits; duplicating that capability alone is not a sufficient result. [AgentAbstain](https://arxiv.org/abs/2607.10059) provides a useful external reference for paired act/abstain tasks, while [τ²-Bench](https://github.com/sierra-research/tau2-bench/blob/main/docs/cli-reference.md) provides a stateful agent evaluation reference. Reuse tasks only where licenses and integration contracts allow it, and keep external benchmark results separate from the in-house corpus.

### B4 — runner overhead and usability

Run a no-model microbenchmark with 100, 1,000, and 10,000 scenarios across small and medium world fixtures. Measure p50/p95 runner overhead, peak memory, and report size on specified hardware. Then have two external teams integrate their agents and write ten scenarios each. Record elapsed setup time, code changes required, ambiguous findings, and cases they could not express.

## Decision gates

1. **Technical pilot:** P0.1–P0.7 complete and the real agent can run without access to production systems.
2. **Public alpha:** versioned package artifacts, documented limitations, and reproducible evaluator correctness results. The v0.2.0a1 alpha packages the current offline-tested functionality; a stable release still requires real-agent and external-team validation.
3. **Broader adoption:** two external teams can integrate and author scenarios, and the comparison shows a concrete reason to use this project alongside or instead of existing tools.

If the comparison finds no meaningful incremental value, narrow the product to the paired scenario authoring layer or contribute that capability to an established project.
