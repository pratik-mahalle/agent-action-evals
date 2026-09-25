# Report contract 1.0

`--json` writes an envelope with `schema_version`, `summary`, and `runs`. `--jsonl` writes one run per line immediately after completion, preserving completed runs if a later run is interrupted. JSON reports are replaced atomically.

Each run contains identifiers, `status`, duration, a version manifest, findings, events, bound tool names, and optional payloads. Status is one of `pass`, `fail`, `error`, or `timeout`. A rejected unknown tool, exceeded call budget, schema violation, or read-only mutation remains a failure even if the agent catches its exception. `error` includes agent/handler exceptions and evaluator failures; it must not be counted as successful evaluation or silently excluded from benchmark denominators.

CLI exit codes are 0 for all passing, 1 for assertion failures, and 2 for configuration or execution errors/timeouts. JUnit represents assertion failures as `failure` and execution failures as `error`.

The manifest includes package/Python versions, scenario/configuration hashes, seed, deadline, and isolation mode. Built-in adapters contribute driver configuration. Custom drivers must implement `manifest()` or include runtime configuration in `Scenario.metadata`. Hashes cover captured source and configuration; they do not promise to capture arbitrary closures, external files, provider-side model changes, or all process state. Seeds identify repetitions and framework inputs; they do not force deterministic LLM output.

Default reports omit input, output, tool arguments/results, state changes, raw exceptions, agent metadata, and final state. Identifiers, assertion paths/rules, tool names, and implementation configuration hashes are public metadata; keep secrets out of these labels. `--include-payloads` enables full local debugging data and should be used with synthetic/scrubbed fixtures. Reports do not collect environment variables or provider credentials.

Events distinguish `tool_attempt`, `tool_executed`, `tool_observation`, `tool_fault`, and `tool_rejected`. Executed events include completion/error/cancellation status and committed changes in full-payload mode. Preconditions are observed before execution; failures do not prevent the action, so the test can expose an unsafe effect. Call counts distinguish attempted and executed calls. Whole-trace ordering assertions require the exact declared sequence.

Findings include `event_seq` and `attribution`. `direct` means the violating event is observed. `direct_transition` means a state value changed away from an already satisfied assertion. `unavailable` means the tool cannot attribute a first cause; the UI must preserve this uncertainty. Final-state equality alone cannot detect temporary unsafe effects that were later repaired: add a precondition or event assertion for that behavior.

Per-case reports include observed pass counts/rates and Wilson intervals. Repeats are not a guarantee of independence; the interval describes the sampled cases and configuration. Keep cases separate when aggregating model results.

## Fault coverage

Run manifests now include an additive `faults` list. Each item records the tool,
fault kind, per-tool call number, and whether the injection actually fired. Stale
observation values are omitted. Fault events include their `call_number`.
These additions preserve report schema version 1.0; consumers should tolerate
new fields.

Generated variants include a `FaultTriggered` assertion. An unexercised injection
is a `fail` with a `fault coverage[...]` finding, `actual="not reached"` in full
payload mode, and no attributed event. It is a coverage failure, not evidence of
an unsafe effect. Handwritten faults only acquire this assertion when explicitly
added; their activation is still recorded in the manifest.

## Readable reports

`--explain` prints a timeline for each failed, errored, or timed-out run. `--text
PATH` atomically writes a plain-text summary, injection coverage, case outcomes,
and failure details. It requires no extra dependency or browser.

The timeline distinguishes attempts, execution, observations, rejected calls,
injected faults, and agent completion. `!` marks an event referenced by a finding;
it does not assert an underlying root cause. Unavailable attribution stays
explicit. Default output includes the number of changed state fields but omits
their names and values, arguments, results, and agent output. Assertion paths and
registered tool names remain public metadata, as in JSON reports.

`--include-payloads` adds escaped values, with each value limited to 600 characters
in text. Use JSON with payloads enabled for untruncated evidence. Neither raw
agent configuration nor arbitrary agent metadata is rendered in the text report.

Rerun commands select the original case, seed, and timeout against the same local
scenario path. Use the project's Python environment. The displayed scenario hash
helps identify source/configuration changes; the command does not restore old
code or force deterministic live model responses.
