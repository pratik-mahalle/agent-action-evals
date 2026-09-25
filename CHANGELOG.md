# Changelog

## 0.2.0a1 — unreleased

- Validate scenarios, schemas, assertions, and variants before invoking an agent.
- Keep rejected calls and contract violations in the verdict even when caught by the agent.
- Record committed effects on handler errors and cooperative cancellation; seal finished worlds.
- Add tool preconditions and explicit failure-attribution confidence.
- Add trusted LangGraph/process adapters and an offline Docker adapter.
- Add a ten-case LangGraph refund demo with scripted responses and a shared live-model entry point.
- Version reports, capture configuration hashes, redact payloads, and checkpoint JSONL runs.
- Add a synthetic conformance corpus, local overhead benchmark, and CI configuration.
- Compare three paired failure cases with a pinned Failproof SDK worker using independent simulations and equivalent state evidence.
- Fix the offline test network guard to allow Windows asyncio's internal loopback sockets.

The JSON report now uses an envelope with `summary` and `runs`. Agents receive `ToolClient` instead of the internal `ToolPort`; use injected tools rather than direct world access.
