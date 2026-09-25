# Reproduce the Failproof SDK comparison

This is a local comparison of evaluation components. It uses Failproof's actual
Python `WorkerRuntime` with a memory transport, plus custom checks and an
independent fixture simulator. It makes no cloud or model calls, installs no
agent hooks, and uses no credentials.

## Run

From the Agent Action Evals repository, after installing `.[dev]`:

```bash
git clone --filter=blob:none --no-checkout \
  https://github.com/FailproofAI/failproofai.git /tmp/aae-failproof

git -C /tmp/aae-failproof sparse-checkout set sdk/python
git -C /tmp/aae-failproof checkout b8cce3b221f94b74b7283a6df63ffb603712776a

python benchmarks/comparison/run.py --failproof-root /tmp/aae-failproof

AAE_FAILPROOF_SOURCE=/tmp/aae-failproof \
  python -m pytest tests/test_comparison.py -q
```

The runner checks the upstream commit and rejects tracked SDK modifications. It
loads the dependency-free SDK from that checkout without installing it globally.
No code from Failproof is copied into this repository. The comparator runs
trusted Python in process and disables socket connections during the comparison.

The missing-evidence control deliberately raises a `ValueError` in our custom
check. An SDK error log for that control is expected. It is recorded separately
from the twelve scored cases and must produce an error rather than a passing
verdict.

Exit status is zero when both evaluators agree with all independent labels, all
observed behavior matches, all three unsafe effects are localized, and the
missing-evidence control errors. Any deviation exits nonzero. A nonzero exit
means the recorded comparison expectation changed; inspect the report before
drawing a product conclusion.

## Files

| File | Responsibility |
| --- | --- |
| `cases.py` | Shared fixtures, policies, tool handlers, and mutation labels |
| `aae.py` | Native AAE scenario, assertions, faults, and driver |
| `failproof_simulator.py` | Custom world reset, tool execution, fault injection, and state telemetry |
| `failproof_checks.py` | Custom outcome/precondition check registered with Failproof |
| `failproof_local.py` | Real SDK worker invocation with a memory replacement for server transport |
| `run.py` | Independent execution, behavior parity, scoring, source hashes, and report generation |

Three families × two fixture conditions × two policies = twelve labeled runs
per evaluator. The safe policy always follows the contract; each mutant changes
one decision and becomes unsafe only in its changed fixture. The policies never
receive labels or expected outcomes.

Results: [recorded JSON](../results/failproof-comparison.json).
Interpretation: [comparison report](../../docs/FAILPROOF_COMPARISON.md).
