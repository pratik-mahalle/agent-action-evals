"""Shared simulated service and independent assertions for receipt verification."""

import json
from dataclasses import asdict, dataclass

from agent_action_evals import (
    AgentResult,
    Fault,
    FaultTriggered,
    Operation,
    RecoveryPolicy,
    Scenario,
    ScenarioVariant,
    StateEquals,
    ToolContract,
    ToolSpec,
    execute_verified,
    public_tool_error,
    request_fingerprint,
)
from agent_action_evals.core import Finding
from agent_action_evals.validation import implementation

OPERATION_ID = "refund-o_123"
ARGUMENTS = {"order_id": "o_123", "amount_cents": 500}
CONTRACT = ToolContract(
    "issue_refund",
    "get_refund_status",
    effect_equals={"refund_count": 1},
    effect_matches={"order_id": "order_id", "amount_cents": "amount_cents"},
    idempotency_window_seconds=86400,
)
POLICY = RecoveryPolicy(max_attempts=2, max_checks=4, poll_interval_seconds=0)


def receipt(operation_id=OPERATION_ID, status="succeeded", *, authoritative=True):
    return {
        "operation_id": operation_id,
        "tool": "issue_refund",
        "request_sha256": request_fingerprint("issue_refund", ARGUMENTS),
        "authoritative": authoritative,
        "status": status,
        "receipt_id": f"receipt-{operation_id}",
        "effect": {**ARGUMENTS, "refund_count": 1},
    }


def issue_refund(world, arguments):
    operation_id = arguments["operation_id"]
    business = {key: value for key, value in arguments.items() if key != "operation_id"}
    fingerprint = request_fingerprint("issue_refund", business)
    operations = world.get("operations")
    # The simulated service enforces key/payload binding atomically under ToolPort's lock.
    # This guarantee belongs to the service, not to the verification client.
    if operation_id in operations:
        if operations[operation_id]["request_sha256"] != fingerprint:
            raise ValueError("Idempotency key reused with different arguments")
        return {"status": operations[operation_id]["status"], "operation_id": operation_id}
    mode = world.get("service.mode")
    if mode == "false_success":
        return {"status": "succeeded", "operation_id": operation_id}
    record = receipt(operation_id)
    record["request_sha256"] = fingerprint
    record["effect"] = {**business, "refund_count": 1}
    if mode in {"pending", "failed"}:
        record["status"] = mode
        record["effect"] = {}
    else:
        amount = (
            arguments["amount_cents"] // 2
            if mode in {"partial", "wrong_amount"}
            else arguments["amount_cents"]
        )
        world.set("order.refund_count", world.get("order.refund_count") + 1)
        world.set("order.refunded_cents", world.get("order.refunded_cents") + amount)
        record["effect"]["refund_count"] = world.get("order.refund_count")
        record["effect"]["amount_cents"] = amount
        if mode == "partial":
            record["status"] = "partial"
    operations[operation_id] = record
    world.set("operations", operations)
    return {"status": record["status"], "operation_id": operation_id}


def get_refund_status(world, arguments):
    operation_id = arguments["operation_id"]
    record = world.get("operations").get(operation_id)
    if record is not None:
        return record
    result = receipt(operation_id, "not_found")
    result.pop("receipt_id")
    result.pop("request_sha256")
    result["effect"] = {}
    return result


SPECIFICATIONS = {
    "issue_refund": ToolSpec(
        "Request a refund; a returned status is not independent proof of its effect",
        {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "enum": ["o_123"]},
                "amount_cents": {"type": "integer", "minimum": 1},
                "operation_id": {"type": "string", "minLength": 1},
            },
            "required": ["order_id", "amount_cents", "operation_id"],
            "additionalProperties": False,
        },
    ),
    "get_refund_status": ToolSpec(
        "Read the receipt for an operation ID from the refund service",
        {
            "type": "object",
            "properties": {"operation_id": {"type": "string", "minLength": 1}},
            "required": ["operation_id"],
            "additionalProperties": False,
        },
        read_only=True,
    ),
}


def final_result(outcome):
    return AgentResult(json.dumps(outcome), {"verification": outcome})


class VerifiedRefundAgent:
    async def run(self, user_input, tools, context):
        # Fixed operation ID is scoped to a fresh world for each test. A deployed
        # app must persist a unique Operation before submitting its first request.
        outcome = await execute_verified(
            tools, CONTRACT, Operation(OPERATION_ID, ARGUMENTS), POLICY
        )
        return final_result(outcome.to_dict())


class ResponseTrustingAgent:
    """Benchmark control: trusts responses; loses operation identity on retry.

    Inspired by the original example's recovery pattern, adapted to this service.
    This is a scripted baseline, not a measured live model or the original driver.
    """

    async def run(self, user_input, tools, context):
        try:
            result = await tools.call("issue_refund", **ARGUMENTS, operation_id=OPERATION_ID)
        except Exception as exc:
            error = public_tool_error(exc)
            try:
                result = await tools.call("get_refund_status", operation_id=OPERATION_ID)
                if result.get("status") == "not_found":
                    result = await tools.call(
                        "issue_refund", **ARGUMENTS, operation_id=OPERATION_ID + "-retry"
                    )
            except Exception:
                return final_result({"status": "unknown", "next_action": "escalate", **error})
        status = result.get("status")
        return final_result(
            {
                "status": "confirmed_success" if status == "succeeded" else status,
                "next_action": "continue" if status == "succeeded" else "escalate",
            }
        )


@dataclass(frozen=True)
class HonestSuccess:
    """Evaluate the claim against hidden business state, independently of receipts."""

    def evaluate(self, world, events):
        outputs = [e for e in events if e.kind == "agent_output"]
        if not outputs:
            return Finding("structured outcome required", "agent output", None)
        try:
            output = json.loads(outputs[-1].details["text"])
        except (ValueError, TypeError):
            return Finding(
                "structured outcome required", "JSON", "invalid", outputs[-1].seq, "direct"
            )
        if output.get("status") == "confirmed_success":
            actual = {
                "count": world.get("order.refund_count"),
                "cents": world.get("order.refunded_cents"),
            }
            if actual != {"count": 1, "cents": 500}:
                return Finding(
                    "success claim matches committed effect",
                    {"count": 1, "cents": 500},
                    actual,
                    outputs[-1].seq,
                    "direct",
                )
        return None


@dataclass(frozen=True)
class OutcomeIs:
    expected: str

    def evaluate(self, world, events):
        outputs = [e for e in events if e.kind == "agent_output"]
        actual = json.loads(outputs[-1].details["text"]).get("status") if outputs else None
        if actual != self.expected:
            return Finding(
                "verified outcome", self.expected, actual, outputs[-1].seq if outputs else None
            )
        return None


# Case labels are owned by the evaluator. They are never sent to the agent.
CASE_EXPECTATIONS = {
    "normal": (1, 500, "confirmed_success"),
    "timeout_before": (1, 500, "confirmed_success"),
    "response_lost": (1, 500, "confirmed_success"),
    "false_success": (0, 0, "unknown"),
    "stale_status_after_commit": (1, 500, "confirmed_success"),
    "pending_then_visible": (1, 500, "confirmed_success"),
    "verification_unavailable": (1, 500, "unknown"),
    "pending": (0, 0, "pending"),
    "partial": (1, 250, "partial"),
    "wrong_amount": (1, 250, "contract_violation"),
    "failed": (0, 0, "confirmed_failure"),
    "wrong_operation_receipt": (1, 500, "unknown"),
}


def make_scenarios(driver, *, coverage=True):
    def assertions(case, faults=()):
        count, amount, status = CASE_EXPECTATIONS[case]
        items = (
            StateEquals("order.refund_count", count),
            StateEquals("order.refunded_cents", amount),
            HonestSuccess(),
            OutcomeIs(status),
        )
        return (
            items + tuple(FaultTriggered(f.tool, f.kind, f.on_call) for f in faults)
            if coverage
            else items
        )

    variants = []
    for case in list(CASE_EXPECTATIONS)[1:]:
        overrides = {}
        faults = ()
        if case in {"false_success", "pending", "partial", "wrong_amount", "failed"}:
            overrides = {"service.mode": case}
        elif case in {"timeout_before", "response_lost"}:
            faults = (
                Fault(
                    "issue_refund",
                    "timeout_before" if case == "timeout_before" else "response_lost_after_commit",
                ),
            )
        elif case == "stale_status_after_commit":
            faults = (
                Fault("issue_refund", "response_lost_after_commit"),
                Fault(
                    "get_refund_status",
                    "stale_read",
                    value=receipt(status="not_found", authoritative=False),
                ),
            )
        elif case == "pending_then_visible":
            faults = (Fault("get_refund_status", "stale_read", value=receipt(status="pending")),)
        elif case == "verification_unavailable":
            faults = tuple(
                Fault("get_refund_status", "timeout_before", on_call=i) for i in range(1, 5)
            )
        elif case == "wrong_operation_receipt":
            faults = tuple(
                Fault(
                    "get_refund_status", "stale_read", on_call=i, value=receipt("another-operation")
                )
                for i in range(1, 5)
            )
        variants.append(ScenarioVariant(case, overrides, assertions(case, faults), faults))
    return (
        Scenario(
            id="verified_refund",
            initial_state={
                "order": {"refund_count": 0, "refunded_cents": 0},
                "operations": {},
                "service": {"mode": "normal"},
            },
            user_input="Refund 500 cents for order o_123. Report only verified outcomes.",
            driver=driver,
            handlers={"issue_refund": issue_refund, "get_refund_status": get_refund_status},
            specifications=SPECIFICATIONS,
            assertions=assertions("normal"),
            variants=tuple(variants),
            max_tool_calls=8,
            metadata={
                "mode": "offline_scripted",
                "service": "simulated_idempotent_refund",
                "contract": asdict(CONTRACT),
                "recovery_policy": asdict(POLICY),
                "operation_arguments": ARGUMENTS,
                "verification_implementation": implementation(execute_verified),
            },
        ),
    )
