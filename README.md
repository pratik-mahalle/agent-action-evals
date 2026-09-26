<p align="center">
  <img src="docs/assets/banner.svg" alt="Agent Action Evals — test what your agent actually does" width="100%" />
</p>

<p align="center">
  <a href="https://github.com/pratik-mahalle/agent-action-evals/actions/workflows/ci.yml"><img src="https://github.com/pratik-mahalle/agent-action-evals/actions/workflows/ci.yml/badge.svg" alt="CI status" /></a>
  <a href="pyproject.toml"><img src="https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&amp;logoColor=white" alt="Python 3.11 or newer" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-CAE989?labelColor=17231B" alt="MIT license" /></a>
  <a href="ROADMAP.md"><img src="https://img.shields.io/badge/status-alpha-E8AA77?labelColor=17231B" alt="Alpha prerelease" /></a>
</p>

<p align="center">
  <a href="#quickstart">Quickstart</a> ·
  <a href="#langgraph-example">LangGraph example</a> ·
  <a href="#benchmarks">Benchmarks</a> ·
  <a href="ARCHITECTURE.md">Architecture</a> ·
  <a href="CONTRIBUTING.md">Contribute</a>
</p>

# Agent Action Evals

**Fault-injection testing for tool-using AI agents.**

Run your agent against controlled business state, inject tool failures, and check what changed. Catch duplicate refunds, actions taken without authorization, and unsafe retries before they reach users.

A refund tool can succeed even when its response times out. An agent may retry, issue a second refund, and still tell the customer everything went well. Agent Action Evals records the tool's effect and the response the agent received, then checks the state and action history against your assertions.

**Status:** [v0.3.0a1 — test existing tools](https://github.com/pratik-mahalle/agent-action-evals/releases/tag/v0.3.0a1). Wrap Python functions and LangGraph tools to inject failures and observe recovery. A live Jev/D1 integration check is recorded; existing-service adapters, external-team pilots, and broader production isolation validation remain open. See the [roadmap](ROADMAP.md).

The current focus is reusable fixtures, controlled tool failures, and assertions against business state. In a [local comparison with Failproof's SDK](docs/FAILPROOF_COMPARISON.md), both approaches detected the same three seeded failures with zero false alarms. Our potential advantage is less simulation plumbing; a broader product advantage remains unproven.

## What you can test

| Question | How it is checked |
| --- | --- |
| Did the agent produce the intended outcome? | Assertions against final world state |
| Did it act when it should have stopped? | Paired cases that change identity, ownership, or eligibility |
| Did a retry repeat an already committed action? | Separate counts for attempted and executed tool calls |
| Were required conditions true when an action ran? | Preconditions evaluated immediately before tool execution |
| What happens when tools fail or return stale data? | Timeouts before execution, lost responses after commit, and stale reads |
| Which action directly violated a rule? | Findings linked to recorded events, with explicit attribution confidence |

Runs start from fresh SQLite state. Reports include JSON, JSONL checkpoints, and JUnit for CI. The core needs no model API key or LLM judge.

## Quickstart

Requires **Python 3.11+**. Install from source:

For the versioned wheel, source archive, and checksums, see the
[release notes](docs/releases/v0.3.0a1.md).

```bash
git clone https://github.com/pratik-mahalle/agent-action-evals.git
cd agent-action-evals

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .

agent-action-evals examples/refund_scenarios.py
```

On Windows, use `python -m venv .venv` and activate with `.venv\Scripts\Activate.ps1` in PowerShell.

The example uses a scripted Python agent and simulated tools. It makes no network or model calls. You should see three passing cases:

```text
PASS refund_order [<run-id>]
PASS refund_order/unverified_caller [<run-id>]
PASS refund_order/response_lost_after_refund [<run-id>]
3/3 runs passed
```

The first case issues a refund. The second changes the caller to unverified and expects no refund. The third loses the refund response after the state changes, so the agent must recover without issuing another refund.

## Reusable refund pack and generated fault cases

Start with **14 ready-made cases** covering authorization, eligibility, stale
reads, read failures at different calls, and safe refund recovery:

```bash
agent-action-evals examples/refund_pack.py --list-cases
agent-action-evals examples/refund_pack.py --repeat 2 \
  --text /tmp/refund-report.txt --json /tmp/refund-report.json
```

The offline example should pass **28/28 runs**. Use
`examples/langgraph_refund_pack.py` for the same pack with the existing LangGraph
demo. A `RefundFixture` maps the pack to your tool names, schemas, handlers, and
business-state paths.

For other domains, `with_fault_matrix(base, FaultPlan("your_tool"))` generates
timeout and lost-response variants. Configure call positions, expected outcomes,
and explicit stale observations as needed. A generated case fails with a coverage
finding if the intended fault never fires.

To inspect a deliberately broken retry policy:

```bash
agent-action-evals examples/refund_pack_unsafe.py \
  --case unsafe_refund/fault-issue_refund-response_lost_after_commit-call-1 \
  --explain --include-payloads
```

This synthetic example should exit **1** and identify the duplicate refund.
Reports show the timeline, findings, fault coverage, and a rerun command.
Payloads stay redacted unless explicitly enabled.

Read the [scenario pack and fault matrix guide](docs/SCENARIO_PACKS.md) for the
policy, custom bindings, generation limits, and failure-report walkthrough.

## How it works

```mermaid
flowchart LR
    S[Scenario + variants] --> R[Runner]
    R --> A[Your agent]
    A --> T[Injected tools]
    T --> F[Fault injection]
    F --> W[Simulated world]
    T --> E[Event history]
    W --> V[Assertions]
    E --> V
    V --> O[JSON / JUnit / CLI]
```

1. **Define the world.** Supply starting state and Python handlers for your tools.
2. **Connect the agent.** Route its tool calls through the injected `ToolClient`.
3. **Describe the outcome.** Assert state, action counts, execution order, or preconditions.
4. **Change the situation.** Add a variant or fault and run the same agent again.
5. **Inspect the evidence.** Read the verdict, observed effects, and related event IDs.

For example, the included refund case defines its expected effect with:

```python
assertions=(
    StateEquals("orders.o_123.refund_count", 1),
    ToolCallCount("issue_refund", 1),
)
```

Its paired case changes one fact and replaces the assertions:

```python
ScenarioVariant(
    id="unverified_caller",
    overrides={"caller.verified": False},
    assertions=(
        StateEquals("orders.o_123.refund_count", 0),
        ToolCallCount("issue_refund", 0),
    ),
)
```

See the complete, runnable [refund scenario](examples/refund_scenarios.py). Scenarios are trusted Python modules exporting `SCENARIOS`; no separate configuration language is required.

### Connect your agent

Implement this interface:

```python
async def run(self, user_input, tools, context) -> AgentResult:
    ...
```

Call tools with `await tools.call(name, **arguments)`. The agent receives a `ToolClient`; your simulated handlers receive the `World`. Supply `ToolSpec` JSON Schemas to validate arguments and results. All tested effects need to pass through the injected tools.

| Integration | Starting point |
| --- | --- |
| Plain Python | [Refund agent and scenarios](examples/refund_scenarios.py) |
| LangGraph | [Offline demo](examples/langgraph_refund_demo.py) and [integration guide](docs/LANGGRAPH_EXAMPLE.md) |
| Subprocess | [Process adapter](src/agent_action_evals/adapters/process.py) and [JSONL client](src/agent_action_evals/remote.py) |
| Docker | [Container example](examples/docker_refund.py) and [isolation guide](docs/ISOLATION.md) |

## LangGraph example

Install the optional integration and run ten cases without credentials:

```bash
python -m pip install -e '.[langgraph]'
agent-action-evals examples/langgraph_refund_demo.py --repeat 3 \
  --json /tmp/langgraph-offline.json
```

Expected: **30/30 runs passed**. A real LangGraph `StateGraph` and `ToolNode` execute scripted model responses against simulated tools. The suite covers verified and unverified callers, wrong owners, ineligible orders, existing refunds, stale reads, and three timeout/response-loss cases.

The live entry point uses the same graph, cases, tools, and assertions. Set `ANTHROPIC_API_KEY` and `AAE_MODEL` locally, then run:

```bash
agent-action-evals examples/langgraph_refund.py --repeat 10 --timeout 120 \
  --json /tmp/langgraph-live.json --jsonl /tmp/langgraph-live.jsonl
```

Live runs make billable provider calls. This Anthropic/LangGraph entry point has not yet been measured with a live model. A separate live Jev decision-loop validation is recorded below. The [LangGraph guide](docs/LANGGRAPH_EXAMPLE.md) covers provider settings, case definitions, and adapting your existing graph.

## Test existing tools

Wrap an existing Python function to inject failures and observe the agent's
recovery. Functions keep their inputs and normal outputs; sync and async calls
remain supported. The wrapper performs no retries or deduplication.

```python
from agent_action_evals import Fault, ToolBoundary

# create_issue is your existing tool function.
boundary = ToolBoundary((Fault("create_issue", "response_lost_after_commit"),))
tested_create_issue = boundary.wrap(create_issue)
# Give tested_create_issue to the agent in place of create_issue.
```

For existing LangGraph tools, use
`wrap_tool_node(existing_tools, boundary, **your_node_options)` from
`agent_action_evals.adapters.langgraph`. It retains native tool schemas, runtime
injection, artifacts, and error handling. Calls, repeated inputs, observations,
and fault coverage are available through `boundary.report()`; payloads are omitted
by default. [Usage and limits](docs/TOOL_BOUNDARY.md).

Try `python examples/wrapped_sqlite_tool.py`: a real local database write succeeds,
its response is hidden, and an independent query finds the committed row. A
wrapper trace alone does not prove an external service committed an effect.

## Experimental: verify tool outcomes during execution

This alpha also includes an **experimental, opt-in verification layer**.
Declare the expected business effect, supply an operation-status tool, and return
structured evidence to the agent. Successful responses are verified; uncertain
results remain `unknown`. Bounded retries require a declared service idempotency
guarantee and reuse the same operation key and payload.

```bash
agent-action-evals examples/verified_refund.py --explain
agent-action-evals examples/langgraph_verified_refund.py --repeat 3
python benchmarks/verification.py --repeat 10
```

The examples cover twelve synthetic cases, including ambiguous timeouts, false
success, stale status, partial effects, and mismatched receipts. They use scripted
policies and simulated tools. See the [verification guide](docs/VERIFICATION.md)
and [benchmark results](benchmarks/results/verification.md).

The helper has also been [validated with live Jev decisions and a real Cloudflare
D1 service](benchmarks/results/live-jev.md): **18/18 verified trials passed**,
with zero false success claims. The direct policy made three false claims in
18 trials. This used a temporary synthetic refund ledger; no payment provider
was involved. See the [live setup guide](benchmarks/live_service/README.md) to reproduce it.

## Use it in CI

Validate before invoking agents, repeat each case, and export reports:

```bash
agent-action-evals examples/refund_scenarios.py --validate
agent-action-evals examples/refund_scenarios.py --repeat 3 \
  --json /tmp/agent-evals.json \
  --jsonl /tmp/agent-evals.jsonl \
  --junit /tmp/agent-evals.xml
```

| Option | Purpose |
| --- | --- |
| `--case refund_order/unverified_caller` | Run one case |
| `--list-cases` | List authored and generated cases without invoking agents |
| `--repeat 10` | Run each selected case ten times with fresh state |
| `--timeout 30` | Set the cooperative deadline for each run, in seconds |
| `--json` / `--jsonl` / `--junit` | Export aggregate reports, checkpoints, or test results |
| `--explain` / `--text` | Print failure timelines or save a readable report with rerun commands |
| `--include-payloads` | Include raw tool values, model output, state, and error details |

Exit codes: **0** for all passing, **1** for assertion failures, **2** for configuration errors, execution errors, or timeouts. Payloads are redacted by default. Reports include per-case pass counts and confidence intervals; see the [report contract](docs/REPORTS.md).

## Benchmarks

The recorded results include evaluator checks, scripted integrations, and a
separate live model/service validation:

| Check | Recorded result | What was measured |
| --- | --- | --- |
| Synthetic evaluator corpus | 40/40 seeded unsafe cases detected; 0 false alarms; 0 runner errors | 160 labeled executions across 40 paired tasks from eight behavior templates |
| Failure attribution | 40/40 first wrong effects localized | The same synthetic corpus |
| Offline LangGraph | 100/100 passing runs | Ten fixed-policy cases, repeated ten times |
| Reusable refund pack | 56/56 passing runs | Fourteen cases, twice each with the scripted Python and LangGraph policies |
| Outcome verification (experimental) | 120/120 passing runs per verified adapter; zero false success claims or duplicate effects | Twelve authored cases, ten repetitions each in Python and LangGraph; [scripted comparison](benchmarks/results/verification.md) |
| Live Jev + D1 | 18/18 verified trials passed; 0 false success claims versus 3 in the direct policy | Six cases, three repetitions per policy; real model and durable cloud ledger; [results and limits](benchmarks/results/live-jev.md) |
| Failproof SDK comparison | Both detected 3/3 unsafe runs and passed 9/9 safe controls | Same policies and matching tool observations; custom state telemetry supplied to Failproof |
| Small-fixture runner overhead | p50 **2.205 ms**, p95 **4.124 ms** | 10,000 local runs; 1,159-byte fixture; no model calls |
| Test suite | 130 local tests passed; 3 skipped | Includes real-callable boundary, verification, and live-harness checks; optional upstream comparison and two Docker checks skipped locally |

Read the [synthetic methodology and full results](benchmarks/results/local.md) and [offline LangGraph results](benchmarks/results/langgraph-offline.md). The synthetic corpus has parameterized cases; its score does not establish reliability on unseen agents or live models. Timings are specific to the recorded machine and workload.

Reproduce the evaluator benchmark:

```bash
python benchmarks/run.py --sizes 100 1000 10000
```

## Execution boundaries

Python and LangGraph integrations run trusted code in the evaluator process. In-process timeouts are cooperative. Plain subprocess execution also assumes trusted agent code.

The Docker adapter is configured with no network, an unprivileged user, a read-only filesystem, and resource limits. It fails if Docker is unavailable. Its two integration checks for enforcement and timeout cleanup [passed in Linux CI](https://github.com/pratik-mahalle/agent-action-evals/actions/runs/36164942542). They remain skipped locally when no Docker daemon is available.

To run the container example and enforcement tests with Docker available:

```bash
docker pull python:3.11-slim
agent-action-evals examples/docker_refund.py
AAE_DOCKER_TESTS=1 python -m pytest tests/test_process.py -q
```

Use synthetic or scrubbed fixtures. Read the [isolation contract](docs/ISOLATION.md) and [security policy](SECURITY.md) before integrating agents with external access.

## Development and contribution

```bash
python -m pip install -e '.[dev,langgraph]'
python -m pytest -q
ruff check src tests examples benchmarks
ruff format --check src tests examples benchmarks
```

Useful contributions include realistic failure scenarios, adapters for existing agents, and clearer failure reports. For a bug report, include a minimal scenario, the expected effect, and a redacted run artifact. See [CONTRIBUTING.md](CONTRIBUTING.md).

| Document | Contents |
| --- | --- |
| [Architecture](ARCHITECTURE.md) | Original design, interfaces, and decisions |
| [Roadmap](ROADMAP.md) | Implemented work and remaining production gates |
| [LangGraph guide](docs/LANGGRAPH_EXAMPLE.md) | Offline and live examples |
| [Scenario packs](docs/SCENARIO_PACKS.md) | Refund fixtures, tool/state mappings, automatic fault cases |
| [Report contract](docs/REPORTS.md) | Artifacts, redaction, and exit statuses |
| [Failproof comparison](docs/FAILPROOF_COMPARISON.md) | Reproducible component comparison and product-scope decision |
| [Changelog](CHANGELOG.md) | Changes in the current prerelease |

## License

[MIT](LICENSE). Built for developers testing agents that act on real systems.
