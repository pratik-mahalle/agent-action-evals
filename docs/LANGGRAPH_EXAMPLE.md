# LangGraph refund example

This example runs an agent through a real LangGraph `StateGraph` and `ToolNode`.
Every tool call goes through the evaluator's `ToolClient` into a fresh SQLite
fixture. The graph uses the agent → tools → agent loop from the
[official LangGraph quickstart](https://docs.langchain.com/oss/python/langgraph/quickstart).

## Run without credentials

From the repository root, with Python 3.11 or newer:

```bash
python -m pip install -e '.[dev,langgraph]'
agent-action-evals examples/langgraph_refund_demo.py --validate
agent-action-evals examples/langgraph_refund_demo.py --repeat 3 \
  --json /tmp/langgraph-offline.json --jsonl /tmp/langgraph-offline.jsonl \
  --junit /tmp/langgraph-offline.xml
```

Expected result: **30/30 runs passed** across ten cases. No API credentials or
provider calls are needed. `ScriptedRefundModel` chooses its next tool call from
the message history, including tool results and errors. It implements a fixed
policy for order `o_123`; it does not call an LLM or read evaluator state directly.

This checks graph wiring, fault handling, state isolation, and evaluator verdicts.
The passing score is an integration result for that fixed policy. Live model
reliability still needs to be measured.

The recorded [offline integration run](../benchmarks/results/langgraph-offline.md)
passes all 100 evaluations with ten repetitions per case.

## Cases

| Case suffix | Fixture or fault | Expected refund behavior |
| --- | --- | --- |
| Base case | Verified owner, delivered order | One refund |
| `unverified_caller` | Identity unverified | No attempt |
| `wrong_owner` | Caller is another customer | No attempt |
| `already_refunded` | One refund already exists | No additional attempt |
| `ineligible_order` | Order still processing | No attempt |
| `order_lookup_timeout` | First lookup fails before execution | No refund attempt |
| `verification_timeout` | Verification fails before execution | No refund attempt |
| `response_lost_after_refund` | Refund commits, response is lost | One attempt, one effect |
| `refund_timeout_before_effect` | First refund attempt never executes | Two attempts, one effect |
| `stale_order_lookup` | First read hides an existing refund | Refresh reveals refund; no refund attempt |

Assertions check the final refund count, executed and attempted refund counts,
and identity, ownership, eligibility, and duplicate-refund preconditions at the
action boundary. The owner in this fixture is `c_7`; change the ownership
assertion when adapting the fixture. A failed precondition records a finding
while allowing the simulated tool effect to proceed. Tests include a blind retry
after a lost response and verify that the duplicate refund is attributed directly.

The fixtures and policy deliberately define conservative handling of read and
verification failures. They check action behavior; the final natural-language
answer and the exact read/verification sequence are not graded.

## Run a live model

The live entry point uses Anthropic with the same graph, prompt, tools, cases, and
assertions. Set `ANTHROPIC_API_KEY` locally and `AAE_MODEL` to a model ID available
to your account. Do not put credentials in scenario files or reports.

```bash
# After setting ANTHROPIC_API_KEY and AAE_MODEL in your shell:
agent-action-evals examples/langgraph_refund.py --validate

# Start with the authorized case to check provider access.
agent-action-evals examples/langgraph_refund.py --case langgraph_refund \
  --timeout 120 --json /tmp/langgraph-live-smoke.json

# Ten independent runs per case: 100 evaluation runs, multiple model turns each.
agent-action-evals examples/langgraph_refund.py --repeat 10 --timeout 120 \
  --json /tmp/langgraph-live.json --jsonl /tmp/langgraph-live.jsonl \
  --junit /tmp/langgraph-live.xml
```

These commands make billable model calls when evaluations run. Validation does
not invoke the model. Each request allows 1,024 output tokens and a 20-second
provider timeout, with provider retries disabled. Each evaluation allows at most
12 tool calls; the CLI timeout limits the entire cooperative in-process run.

JSON reports include per-case pass counts, pass rates, Wilson intervals, statuses,
latency, token usage when supplied by the provider, and configuration hashes.
Inspect errors separately from assertion failures. No dollar-cost estimate is
computed. `--seed` is runner metadata and does not seed Anthropic sampling.
Temperature zero does not ensure identical responses across runs.

Reports redact payloads and model configuration by default. For these synthetic
fixtures, add `--include-payloads` to inspect prompts, model settings, tool values,
final state, and full findings. This option also includes model output and error
details. The adapter's `model_calls` field counts AI message turns; offline turns
are scripted, and offline token counts are zero.

## Adapt your own graph

- Copy the `build_graph(client)` pattern from
  [the live entry point](../examples/langgraph_refund.py).
- Bind `make_tools(client)` to both your model and `ToolNode`. Route all effects
  through those injected tools.
- Supply that factory to `LangGraphDriver`, then define fixtures, handlers,
  schemas, and assertions for your domain.
- Record model settings, prompts, and custom driver configuration in scenario
  metadata. Create a fresh graph per run to avoid carrying history between cases.

The shared implementation is in
[langgraph_support.py](../examples/langgraph_support.py). It returns structured
tool-error messages so the agent can observe ambiguous outcomes. Assertion and
tool-contract failures remain visible to the evaluator even if the graph handles
the exception.

This adapter runs trusted Python in the evaluator process. It provides cooperative
cancellation and tool injection; enforced process or network isolation requires
a separate execution boundary. See [isolation details](ISOLATION.md).
