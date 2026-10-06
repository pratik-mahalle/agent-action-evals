# Failproof workflow pilot

This reviewed seed suite checks coding-agent hook decisions for this project. The five ordinary cases cover public Python configuration and comparison docs, the existing test command, the offline refund example, and a documentation edit. Two synthetic probes require refusal: a protected key write and catastrophic deletion.

Cases were derived from the documented workflows at `651f895c4b6edd17884812ff90ee0bb24b1d24bf`. They are independent `PreToolUse` calls, with no recorded production incident or session history. The runner passes command and file-write payloads to policy hooks as data; it never executes those commands or writes those files. Application tests remain in the project's existing validation workflow.

## Reproduce

The CI trial uses Node 22, `failproofai@1.0.9`, all 38 policies from `FailproofAI/policies@06b802b63f4f`, and [runner commit 177eb3d15720b087a2e2c01d878ea559b65f7f0b](https://github.com/pratik-mahalle/failproof-chaos/commit/177eb3d15720b087a2e2c01d878ea559b65f7f0b). No tracked Failproof hook configuration was found, so this is an explicit trial configuration.

Install the same engine and pack in a disposable `FAILPROOFAI_HOME`, check out the pinned runner separately, then run from this project's root:

```bash
mkdir -p .failproofai
node /path/to/failproof-chaos/run.mjs --corpus guardrails/team-cases.json --ci
```

The empty project marker anchors path policies at this directory. A passing run allows all five normal cases and denies both risky probes. CI preserves `results.json` and `REPORT.md`; each report includes the synthetic payloads and policy reasons. A failed expectation keeps CI red even if the same verdict is stored in a baseline.

## Review the next change

Keep expectations tied to intended behavior. Investigate unexpected allowances and unwanted blocks before changing fixtures. If you add a real failure, replace sensitive values with synthetic data and verify that it still reproduces the decision. Review any policy configuration change alongside the cases it affects.

On the next policy or workflow change, record the run link, any actionable finding, authoring or maintenance effort, and which manual checks the suite saved. Repeat use and feedback are needed to decide whether this pilot provides recurring value.
