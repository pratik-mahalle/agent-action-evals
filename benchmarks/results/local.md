# Local benchmark results

Synthetic evaluator conformance; no model API calls.

Environment: {'python': '3.11.13', 'platform': 'macOS-26.5.2-arm64-arm-64bit', 'machine': 'arm64', 'package': '0.2.0a1'}

Eight scripted behavior templates × five fixtures = 40 paired tasks; 160 labeled runs.
Detected 40/40 seeded unsafe cases; 0 false alarms; 0 runner errors.
Localized the first wrong physical effect in 40/40 unsafe cases.

| Runs | Fixture bytes | p50 ms | p95 ms | Runs/sec | Peak Python bytes (20 runs) |
| --- | --- | --- | --- | --- | --- |
| 100 | 1159 | 2.208 | 3.801 | 431.7 | 68397 |
| 100 | 65671 | 4.589 | 6.008 | 218.8 | 362311 |
| 1000 | 1159 | 2.268 | 4.195 | 411.1 | 77181 |
| 1000 | 65671 | 4.510 | 6.332 | 216.0 | 367624 |
| 10000 | 1159 | 2.205 | 4.124 | 423.3 | 77181 |
| 10000 | 65671 | 4.567 | 6.485 | 211.5 | 367625 |

Timing includes validation, hashing, fixture creation, two tool calls, assertions, and cleanup.
Memory is Python allocation peak during a separate 20-run sample, not process RSS.
These parameterized scripts test known failure mechanics. They do not establish real-agent
reliability, generalization to new failures, sandbox security, or superiority to another framework.
