# Production and benchmark plan

**Status:** Proposed work plan, 25 September 2026

**Target:** A public Python package that external teams can run safely and repeatably in CI against their own agents.

The current prerelease has 46 passing tests on Python 3.11 and 3.14, a ten-case LangGraph example with 100/100 passing offline runs, and a synthetic benchmark. A mutation test verifies that a duplicate refund fails evaluation. Two real container tests require an available Docker daemon and were skipped locally. Live model quality, cross-platform CI execution, competing-tool comparisons, and external-team adoption remain unverified.

## Implementation status

| Area | Implemented and checked | Remaining gate |
| --- | --- | --- |
| P0.1 isolation | Docker adapter, bounded protocol, restricted container configuration, explicit failure if unavailable; subprocess lifecycle tests pass | Run the two real Docker enforcement tests; local daemon was unavailable |
| P0.2 integration | LangGraph adapter, ten shared offline/live cases, real ToolNode and unsafe-retry tests, configurable Anthropic example | Configure model credentials and benchmark a team's actual agent |
| P0.3 execution | Serialized tool effects, committed-state capture on errors/cancellation, sealed worlds, subprocess cleanup | Host Python is trusted/cooperative; enforced agent cancellation needs Docker verification |
| P0.4 validation | Preflight IDs, cases, paths, contradictions, schemas, fault targets; rejected calls remain failures | Broader pilot feedback on custom domains |
| P0.5 reproducibility | Scenario/configuration/source hashes, versions, seed/deadline metadata, immutable Docker image ID | Custom driver configuration must be declared; external service state is not captured |
| P0.6 reporting | Versioned JSON envelope, JSONL checkpoints, JUnit classification, default payload redaction | Backward compatibility across future releases |
| P0.7 attribution | Direct violating events and transitions are labeled; uncertain attribution remains explicit | Validate explanations on real production incidents |
| P0.8 release | Public GitHub repository, private vulnerability reporting, dependency lockfile, CI matrix, source/wheel builds, clean-wheel example, contributor/reporting docs | Verify the hosted CI matrix and publish a tagged release |
| B1 evaluator | 160 labeled executions, 40 seeded unsafe cases detected, 0 false alarms | Eight parameterized templates provide conformance evidence, not broad generalization |
| B2 live reliability | Shared live example and per-case repeated-run reports; offline graph run passes 100/100 | Model credentials, selected agent, held-out real cases |
| B3 comparison | Method specified below | Execute fair comparisons against existing tools |
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
2. **First public release:** P0.8 complete; the evaluator correctness corpus and results are published with the package.
3. **Broader adoption:** two external teams can integrate and author scenarios, and the comparison shows a concrete reason to use this project alongside or instead of existing tools.

If the comparison finds no meaningful incremental value, narrow the product to the paired scenario authoring layer or contribute that capability to an established project.
