# Agent Action Evals: architecture proposal

**Status:** Design proposal, 25 September 2026

**Audience:** Developers shipping tool-using agents to production

**Initial implementation:** Python library and CLI

## Product contract

Agent Action Evals runs an existing agent against a controlled, stateful environment and answers three questions:

1. Did the agent achieve the requested outcome?
2. Did it obey action boundaries along the way?
3. What was the first step that made the run fail?

The first use case is a support agent that reads orders and issues refunds. This is an example domain, not a domain dependency. A team supplies its own tool handlers, state fixture, and assertions. The framework owns scenario execution, isolation, event capture, evaluation, and reporting.

## System shape

```mermaid
flowchart LR
    CI[CLI / pytest / CI] --> R[Scenario runner]
    S[Scenario definitions] --> R
    R --> W[Isolated world state]
    R --> D[Agent driver]
    D --> A[Existing agent]
    A --> P[Tool port]
    P --> F[Fault controller]
    F --> T[Simulated tool handlers]
    T --> W
    P --> E[Append-only event log]
    D --> E
    W --> O[State oracle]
    E --> O
    O --> V[Assertion engine]
    S --> V
    V --> Q[JSON / JUnit / human report]
    Q --> CI
```

The **tool port** is the required integration point. The production agent must call injected test tools for the run. The runner never infers a real-world side effect from the agent's final text or from a model's judgment. It checks the simulated world's state and physical tool-call log.

## Components and interfaces

| Component | Responsibility | First-release interface |
| --- | --- | --- |
| Scenario | Declares initial state, user input, variants, faults, and assertions | Python definitions, with serializable scenario metadata |
| Agent driver | Starts an existing agent and returns its final response | `async run(input, tools, context) -> AgentResult` |
| Tool port | Routes all test tool calls through one observable boundary | Named async functions with JSON-compatible arguments/results |
| World | Holds business state and implements tool effects | Per-run SQLite database and pluggable Python handlers |
| Fault controller | Changes what the agent observes without falsifying the world state | Ordered rules at tool invocation, before response, and after commit |
| Event log | Records attempted calls, physical effects, observations, and agent output | Versioned JSONL events, scoped to one run |
| State oracle | Reads authoritative final state and event history | Read-only queries and state selectors |
| Assertion engine | Produces deterministic pass/fail findings | State, event count/order, forbidden action, and budget assertions |
| Reporter | Explains the failing assertion and earliest relevant event | Terminal, JSON, and JUnit output |

The core supports any model provider because it calls the team's agent through `AgentDriver`. One example adapter should demonstrate a real agent framework; custom agents can use the interface directly. A later MCP proxy can serve agents whose tool implementations cannot be injected in process. This proxy is a separate adapter, not a condition for the first release.

## Scenario model

One scenario is a tuple of **starting world state, user request, agent configuration, tool behavior, and expected properties**. A variant overrides one or more facts while inheriting the base case. The runner records exactly which facts changed.

Example, shown as a readable representation of the Python scenario API:

```yaml
id: refund_order
given:
  orders:
    o_123:
      customer_id: c_7
      status: delivered
      refund_count: 0
  caller:
    customer_id: c_7
    verified: true
when:
  user: "Refund order o_123"
expect:
  state:
    orders.o_123.refund_count: 1
  calls:
    issue_refund: {count: 1}
variants:
  - id: unverified_caller
    override:
      caller.verified: false
    expect:
      state:
        orders.o_123.refund_count: 0
      calls:
        issue_refund: {count: 0}
```

The paired variant tests the decision boundary. Text quality can be assessed by a separate optional evaluator, but the refund outcome is checked against authoritative state.

## Run lifecycle

1. Validate the scenario and its assertion paths before invoking a model.
2. Create a fresh world from the fixture. Assign a run ID and scenario version hash.
3. Bind simulated tools to the agent driver. Deny any tool call with no registered test handler.
4. Send the user request to the agent. The agent may make multiple model and tool calls before returning.
5. For each tool call, record intent, apply any fault rule, execute the handler if allowed, record the physical state effect, and return the configured observation.
6. Stop on agent completion, step limit, time limit, or an unrecoverable driver error.
7. Query final world state and evaluate assertions against state plus the event log.
8. Save the run artifact and report the first violated assertion with related events.

Each repeat starts from the same fixture. The model may choose different paths, so the report shows passes over total runs and individual failures. A fixed seed controls framework-generated faults and scripted users; it does not promise deterministic model output.

## The key distinction: observed result versus actual effect

The tool port records two separate facts:

- **Effect:** whether a handler committed a state change.
- **Observation:** the response delivered to the agent.

This allows a scenario where `issue_refund` succeeds but its response is lost. If the agent retries and issues a second refund, the test fails on `refund_count == 1` and points to the second physical effect. It also supports stale reads, timeouts, malformed results, and explicit tool errors. Faults are deterministic per run and visible in the artifact.

## Evaluation model

**Required deterministic assertions:** final state selectors, tool call counts, forbidden calls, event ordering, and limits on steps, duration, or spend when available. A failed assertion includes expected value, actual value, and supporting event IDs.

**Optional subjective assertions:** response helpfulness, tone, or whether a clarification question is understandable. These may use a human label or an LLM judge. The report labels their method and does not let a subjective score override a failed state assertion.

**First-failure attribution:** the engine identifies the earliest event that directly violates a temporal rule, or the first event that changed an asserted state path to the wrong value. For other final-state failures, it reports the relevant event sequence and says attribution is uncertain. It does not claim to infer the model's internal cause.

## Safety and data handling

Runs use isolated local fixtures and test credentials. Tool names and arguments are allowlisted by the scenario; an unknown call at the tool port fails closed. Assertions see exact events in memory, while persisted artifacts default to redacted arguments and results, with explicit opt-in for full payloads. Scenario fixtures should contain synthetic or scrubbed data.

The integration contract requires **all effectful tools** to pass through the tool port. An agent that makes direct network or database calls can bypass the harness. The runner therefore reports which tools were bound and requires teams to remove production credentials from the test process. CI users should disable outbound network access when feasible. A later sandbox adapter can enforce this boundary rather than relying on integration discipline.

Exporting traces to OpenTelemetry is optional. Internal event types remain versioned and stable independently of evolving GenAI semantic conventions. The event log is the source for exact effect assertions; exported spans provide interoperability with existing observability tools.

## First-release scope

1. Python package with `Scenario`, `World`, `AgentDriver`, `ToolPort`, and assertion APIs.
2. Local SQLite world with a reference refund domain and three tool handlers: `get_order`, `verify_customer`, `issue_refund`.
3. Paired scenario variants and three faults: timeout before execution, response loss after commit, and stale read.
4. Deterministic state, call, order, and forbidden-action assertions.
5. CLI for local and CI runs; terminal, JSON, and JUnit reports.
6. One adapter example against an existing Python agent framework and one plain Python agent example.

The first release is useful if a developer can wrap an existing agent's tools, express a real incident as a scenario, run it locally, and make CI fail on a repeatable unsafe effect.

## Validation gates

Before expanding integrations or building a dashboard:

- Reproduce at least three real failure patterns from production agent teams, with permission to use scrubbed examples.
- Have two external teams integrate the tool port with their own agent code and author ten scenarios each.
- Confirm that the report identifies the wrong physical effect and its triggering call without manual trace inspection.
- Compare the setup cost and findings with the teams' current eval workflow.

## Architecture decisions to revisit

- **Python first:** fastest path to a usable test runner and pytest integration. Add a language-neutral subprocess or MCP adapter after validating the core scenario model.
- **Code-defined scenarios first:** teams can write domain-specific state handlers and assertions without a constrained configuration language. Export a declarative format once common patterns emerge.
- **Local state first:** keeps each run isolated and cheap. Remote test environments can be supported through a future `World` adapter with explicit reset and snapshot contracts.

## Related work

Existing tools already cover large parts of evaluation and debugging. [Langfuse](https://langfuse.com/docs), [Phoenix](https://arize.com/docs/phoenix/), and [Promptfoo](https://www.promptfoo.dev/docs/tracing/) provide tracing, datasets, and agent evaluations. [Agent Crash Test](https://github.com/pavloparaschakis/agent-crash-test) checks tool effects under injected failures. [Tau2 Bench](https://arxiv.org/abs/2506.07982) demonstrates stateful agent evaluation in simulated domains. The proposed focus is a small, reusable test harness for a team's own agent and business state, centered on paired action-boundary cases and authoritative effects. This positioning needs user validation.
