# Changelog

## 1.0.0 — 2026-09-26

First stable release. See the [release notes](docs/releases/v1.0.0.md).

- Publish v1.0.0 packages and a regular GitHub release, marked as the latest release.
- Establish the documented public API and report compatibility policy for 1.x.
- Update installation commands and current documentation to the stable version.
- Carry forward the tested tool-boundary wrappers, fault injection, traces,
  scenario runner, and integrations from v0.3.0a1 with no runtime API changes.
- Keep the optional receipt-verification helper explicitly experimental and
  retain the published benchmark scope and known execution limits.

## 0.3.0a1 — 2026-09-26

Test existing tools with boundary fault injection. See the
[release notes](docs/releases/v0.3.0a1.md) for installation, validation, and limits.

- Add `ToolBoundary` for existing synchronous/asynchronous Python callables and
  `wrap_tool_node` for native LangGraph tools. Preserve normal inputs/results,
  inject boundary faults, record concurrent calls and repeated inputs, and leave
  retries to the caller. Payload capture is opt-in; both ambiguous timeout kinds
  expose the same public exception. Add a SQLite write/response-loss example.
- Add opt-in tool outcome verification with operation IDs, request fingerprints,
  declared effect postconditions, and read-only status hooks.
- Return structured confirmed, pending, partial, uncertain, and contract-violation
  outcomes with evidence and bounded recovery decisions.
- Gate write retries on an explicit service idempotency window; retain the exact
  operation key/payload, check expired keys without resubmitting, and support resume.
- Normalize the original LangGraph example's before-execution and after-commit
  timeout errors to the same agent-visible observation.
- Add twelve-case Python and real LangGraph examples and a reproducible scripted
  comparison benchmark with false-success, duplicate-effect, completion, and latency metrics.
- Test cancellation after commit, missing verification evidence, mismatched receipts,
  expired operation keys, and independent evaluation of success claims.
- Add a live Jev decision-agent benchmark against an authenticated Cloudflare D1
  test service, with six fault cases, blinded model observations, persisted operation
  IDs, independent state checks, and separate false/unverified success metrics.

## 0.2.0a1 — 2026-09-26

First public alpha. See the [release notes](docs/releases/v0.2.0a1.md) for installation, assets, and known limitations.

- Add a reusable 14-case refund pack with custom tool, schema, and state bindings.
- Generate bounded fault matrices with stable IDs and explicit fault-activation assertions.
- Add readable failure timelines, local rerun commands, text artifacts, and case discovery.
- Record fault activation in run manifests and validate stale observations against result schemas.
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
