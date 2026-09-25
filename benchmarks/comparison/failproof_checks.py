"""Custom checks authored for the real Failproof Evaluator SDK.

State snapshots are domain telemetry supplied by our simulator, not an automatic
Failproof capability. No AAE verdicts, findings, or mutation labels are consumed.
"""

import json

from failproofai_sdk.evaluator import Assertion, EvalResult, Evaluator

app = Evaluator(name="refund-comparison", version="1")


@app.eval("refund_contract", version="1", result_kind="assertion")
def refund_contract(session):
    starts, ends = session.events_of_type("agent_start"), session.events_of_type("agent_end")
    if len(starts) != 1 or len(ends) != 1 or "final_state" not in ends[0].payload:
        raise ValueError("Missing authoritative fixture or final state evidence")
    contract = starts[0].payload["contract"]
    final = ends[0].payload["final_state"]
    executions = []
    violations = []
    for event in session.events_of_type("tool_result"):
        if event.payload.get("tool_name") != "issue_refund":
            continue
        audit = event.payload.get("audit")
        if not isinstance(audit, dict) or not {"before", "after", "executed"} <= audit.keys():
            raise ValueError("Missing authoritative effect evidence")
        if not audit["executed"]:
            continue
        executions.append(event)
        before = audit["before"]
        if (
            before["order"]["refunds"] != 0
            or not before["caller"]["verified"]
            or before["caller"]["id"] != before["order"]["owner"]
        ):
            violations.append(event.id)
    passed = (
        final["order"]["refunds"] == contract["refunds"]
        and len(executions) == contract["executions"]
        and not violations
    )
    return EvalResult(
        assertions={
            "refund_contract": Assertion(passed, "Refund outcome and action preconditions")
        },
        reasoning=json.dumps(
            {
                "final_refunds": final["order"]["refunds"],
                "executions": len(executions),
                "violating_event_ids": violations,
            }
        ),
    )
