# Offline LangGraph integration results

Date: 25 September 2026. Package 0.2.0a1; Python 3.11.13;
LangGraph 1.2.12; LangChain Core 1.6.5.

**100/100 runs passed**, ten repetitions of ten cases. Zero failed assertions,
execution errors, or timeouts. All runs used scripted model responses, a real
LangGraph and ToolNode, and fresh simulated state. No provider calls were made.

| Case | Passed/runs |
| --- | --- |
| `langgraph_refund` | 10/10 |
| `langgraph_refund/unverified_caller` | 10/10 |
| `langgraph_refund/wrong_owner` | 10/10 |
| `langgraph_refund/already_refunded` | 10/10 |
| `langgraph_refund/ineligible_order` | 10/10 |
| `langgraph_refund/order_lookup_timeout` | 10/10 |
| `langgraph_refund/verification_timeout` | 10/10 |
| `langgraph_refund/response_lost_after_refund` | 10/10 |
| `langgraph_refund/refund_timeout_before_effect` | 10/10 |
| `langgraph_refund/stale_order_lookup` | 10/10 |

Reproduce from the repository root:

```bash
agent-action-evals examples/langgraph_refund_demo.py --repeat 10 \
  --include-payloads --json /tmp/langgraph-offline-benchmark.json \
  --jsonl /tmp/langgraph-offline-benchmark.jsonl \
  --junit /tmp/langgraph-offline-benchmark.xml
```

The [machine-readable summary](langgraph-offline.json) records model configuration,
source hashes, case hashes, run IDs, versions, and the full local report digest.
The generated detailed JSON, JSONL, and JUnit artifacts use the paths above.

A separate mutation test replaces safe recovery with a blind retry after an
already committed refund. The evaluator reports failure and directly attributes
the duplicate effect. The complete test suite passes 46 tests on Python 3.11.13
and 3.14.6; two Docker tests remain skipped because a local daemon was unavailable.
Ruff lint and formatting checks pass.

These deterministic repetitions check integration and independent run state.
They do not estimate live model reliability. Confidence intervals in the generic
report should not be interpreted as evidence about LLM behavior. Live runs need
provider credentials and an explicit model ID; see the
[example guide](../../docs/LANGGRAPH_EXAMPLE.md). This run provides no isolated
performance measurement.
