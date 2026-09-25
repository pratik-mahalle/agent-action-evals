# Report contract 1.0

`--json` writes an envelope with `schema_version`, `summary`, and `runs`. `--jsonl` writes one run per line immediately after completion, preserving completed runs if a later run is interrupted. JSON reports are replaced atomically.

Each run contains identifiers, `status`, duration, a version manifest, findings, events, bound tool names, and optional payloads. Status is one of `pass`, `fail`, `error`, or `timeout`. A rejected unknown tool, exceeded call budget, schema violation, or read-only mutation remains a failure even if the agent catches its exception. `error` includes agent/handler exceptions and evaluator failures; it must not be counted as successful evaluation or silently excluded from benchmark denominators.

CLI exit codes are 0 for all passing, 1 for assertion failures, and 2 for configuration or execution errors/timeouts. JUnit represents assertion failures as `failure` and execution failures as `error`.

The manifest includes package/Python versions, scenario/configuration hashes, seed, deadline, and isolation mode. Built-in adapters contribute driver configuration. Custom drivers must implement `manifest()` or include runtime configuration in `Scenario.metadata`. Hashes cover captured source and configuration; they do not promise to capture arbitrary closures, external files, provider-side model changes, or all process state. Seeds identify repetitions and framework inputs; they do not force deterministic LLM output.

Default reports omit input, output, tool arguments/results, state changes, raw exceptions, agent metadata, and final state. Identifiers, assertion paths/rules, tool names, and implementation configuration hashes are public metadata; keep secrets out of these labels. `--include-payloads` enables full local debugging data and should be used with synthetic/scrubbed fixtures. Reports do not collect environment variables or provider credentials.

Events distinguish `tool_attempt`, `tool_executed`, `tool_observation`, `tool_fault`, and `tool_rejected`. Executed events include completion/error/cancellation status and committed changes in full-payload mode. Preconditions are observed before execution; failures do not prevent the action, so the test can expose an unsafe effect. Call counts distinguish attempted and executed calls. Whole-trace ordering assertions require the exact declared sequence.

Findings include `event_seq` and `attribution`. `direct` means the violating event is observed. `direct_transition` means a state value changed away from an already satisfied assertion. `unavailable` means the tool cannot attribute a first cause; the UI must preserve this uncertainty. Final-state equality alone cannot detect temporary unsafe effects that were later repaired: add a precondition or event assertion for that behavior.

Per-case reports include observed pass counts/rates and Wilson intervals. Repeats are not a guarantee of independence; the interval describes the sampled cases and configuration. Keep cases separate when aggregating model results.
