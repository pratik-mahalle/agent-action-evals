# Versioning and compatibility

v1.0.0 is the first stable package release. The 1.x compatibility scope is the
documented public API, not every importable implementation detail.

## Public API

The supported surface includes the documented scenario/fault/assertion APIs,
`ToolBoundary.wrap()`, `ToolBoundary.events`, `ToolBoundary.report()`,
`run_scenario()`, the CLI, public adapters (including `wrap_tool_node()`), and the
refund scenario pack. The optional verification helper has a separate status below.

- Patch versions fix bugs without intentionally breaking documented valid usage.
- Minor versions add compatible functionality. Optional parameters and additive
  report fields may be introduced; report consumers should tolerate unknown fields.
- Breaking changes to the supported public surface require a new major version.
  Deprecations and migration instructions will be recorded in the changelog.
- Names beginning with `_`, undocumented implementation details, example internals,
  benchmark scripts, and internal event timing are outside the compatibility scope.

Correctness fixes may reject input that violates an existing documented contract.
Bug fixes can also change a test verdict when the prior verdict was incorrect;
such changes will be described in the changelog.

## Reports and integrations

Runner reports retain [schema version 1.0](REPORTS.md). Standalone tool-boundary
reports retain `kind: "tool_boundary"` and their own `schema_version: "1.0"`, as
documented in the [boundary guide](TOOL_BOUNDARY.md). They are distinct contracts.

Python 3.11+ is supported within the tested dependency constraints. Optional
adapters depend on their framework's supported APIs; the LangGraph boundary
requires ToolNode's sync and async wrapper hooks. Provider behavior, SDK-internal
retries, and live model output are outside this package's compatibility guarantee.

## Experimental verification helper

`Operation`, `RecoveryPolicy`, `ToolContract`, `ToolOutcome`, `execute_verified()`,
`public_tool_error()`, and `request_fingerprint()` are documented in the
[verification guide](VERIFICATION.md). This opt-in surface remains experimental
within v1.0.0. Changes will be documented, but its API is not covered by the 1.x
compatibility commitment until explicitly promoted. Pin a package version when
depending on this helper.

## Validation scope

Stable versioning describes the release and compatibility policy. Existing
execution limits, authored benchmark cases, and outstanding production-team
pilots remain documented in the [roadmap](../ROADMAP.md) and
[isolation guide](ISOLATION.md). No release label guarantees an agent's behavior
or an external service's idempotency.
