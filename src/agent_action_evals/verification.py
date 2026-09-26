"""Optional receipt verification using only the agent's permitted tool interface.

Service adapters must provide the documented receipt contract. This module has
no access to World, runner events, injected faults, or evaluator assertions.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import math
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from .core import ToolClient, ToolResponseLost, ToolTimeout
from .state import canonical, same


def request_fingerprint(tool: str, arguments: Mapping[str, Any]) -> str:
    """Bind a receipt to the tool and exact business arguments, excluding its key."""
    return hashlib.sha256(
        canonical({"tool": tool, "arguments": dict(arguments)}).encode()
    ).hexdigest()


def public_tool_error(error: Exception) -> dict[str, str]:
    """Before-execution and after-commit timeouts are indistinguishable to agents."""
    timeout = isinstance(error, (ToolTimeout, ToolResponseLost, TimeoutError))
    return {"error": "tool_timeout" if timeout else "tool_error", "outcome": "unknown"}


@dataclass(frozen=True)
class Operation:
    """Caller-owned identity. Persist ID, arguments AND created_at before sending."""

    id: str
    arguments: Mapping[str, Any]
    created_at: float = field(default_factory=time.time)

    def __post_init__(self):
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("An operation requires a nonempty persistent ID")
        if not math.isfinite(self.created_at):
            raise ValueError("created_at must be a finite Unix timestamp")
        canonical(dict(self.arguments))
        object.__setattr__(self, "arguments", copy.deepcopy(dict(self.arguments)))


@dataclass(frozen=True)
class ToolContract:
    tool: str
    status_tool: str
    effect_equals: Mapping[str, Any] = field(default_factory=dict)
    effect_matches: Mapping[str, str] = field(default_factory=dict)
    operation_key: str = "operation_id"
    # Zero disables automatic write retries. A positive value declares a SERVER
    # guarantee covering this tool, identical payload, key scope, and time window.
    idempotency_window_seconds: float = 0

    def __post_init__(self):
        if not all(
            isinstance(x, str) and x for x in (self.tool, self.status_tool, self.operation_key)
        ):
            raise ValueError("Tool and operation-key names must be nonempty strings")
        if self.tool == self.status_tool:
            raise ValueError("Verification requires a separate status tool")
        if (
            not math.isfinite(self.idempotency_window_seconds)
            or self.idempotency_window_seconds < 0
        ):
            raise ValueError("Idempotency window must be finite and nonnegative")
        if not self.effect_equals and not self.effect_matches:
            raise ValueError("Declare at least one business-effect postcondition")
        if set(self.effect_equals) & set(self.effect_matches):
            raise ValueError("A postcondition field cannot have two definitions")
        canonical(dict(self.effect_equals))
        if any(
            not isinstance(k, str) or not isinstance(v, str) for k, v in self.effect_matches.items()
        ):
            raise ValueError("effect_matches maps effect fields to argument names")
        object.__setattr__(self, "effect_equals", copy.deepcopy(dict(self.effect_equals)))
        object.__setattr__(self, "effect_matches", dict(self.effect_matches))


@dataclass(frozen=True)
class RecoveryPolicy:
    max_attempts: int = 2
    max_checks: int = 4
    timeout_seconds: float = 5
    poll_interval_seconds: float = 0.05

    def __post_init__(self):
        if any(type(v) is not int or v < 1 for v in (self.max_attempts, self.max_checks)):
            raise ValueError("Attempt and check budgets must be positive integers")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("Timeout must be finite and positive")
        if not math.isfinite(self.poll_interval_seconds) or self.poll_interval_seconds < 0:
            raise ValueError("Poll interval must be finite and nonnegative")


@dataclass(frozen=True)
class ToolOutcome:
    operation_id: str
    transport: str
    status: str
    next_action: str
    reason: str
    attempts: int
    checks: int
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _assess(receipt, contract, operation):
    if not isinstance(receipt, dict):
        return "unknown", "invalid_receipt"
    expected = {
        "operation_id": operation.id,
        "tool": contract.tool,
        "authoritative": True,
    }
    if any(key not in receipt or not same(receipt[key], value) for key, value in expected.items()):
        return "unknown", "untrusted_or_mismatched_receipt"
    status = receipt.get("status")
    if status == "not_found":
        # Absence is never proof that an in-flight write cannot still commit.
        return "unknown", "not_found"
    if receipt.get("request_sha256") != request_fingerprint(contract.tool, operation.arguments):
        return "unknown", "untrusted_or_mismatched_receipt"
    if status == "pending":
        return "pending", "operation_pending"
    if not isinstance(receipt.get("receipt_id"), str) or not receipt["receipt_id"]:
        return "unknown", "missing_receipt_id"
    if status == "failed":
        return "confirmed_failure", "service_reported_failure"
    if status == "partial":
        return "partial", "partial_effect"
    if status != "succeeded" or not isinstance(receipt.get("effect"), dict):
        return "unknown", "invalid_receipt"
    expected_effect = dict(contract.effect_equals)
    expected_effect.update(
        {key: operation.arguments[arg] for key, arg in contract.effect_matches.items()}
    )
    if any(
        key not in receipt["effect"] or not same(receipt["effect"][key], value)
        for key, value in expected_effect.items()
    ):
        return "contract_violation", "unexpected_effect"
    return "confirmed_success", "postconditions_verified"


async def execute_verified(
    tools: ToolClient,
    contract: ToolContract,
    operation: Operation,
    policy: RecoveryPolicy = RecoveryPolicy(),
    *,
    resume: bool = False,
) -> ToolOutcome:
    """Execute and verify; use resume=True after an uncertain prior invocation.

    Budgets apply to this invocation. Persist application-wide attempt/deadline
    budgets across resumes. Cancellation propagates; no write is assumed rolled back.
    """
    # Snapshot mutable mappings before any await or side effect.
    contract = copy.deepcopy(contract)
    operation = copy.deepcopy(operation)
    if contract.operation_key in operation.arguments:
        raise ValueError("Business arguments must not contain the reserved operation key")
    if any(arg not in operation.arguments for arg in contract.effect_matches.values()):
        raise ValueError("A postcondition references a missing operation argument")
    spec = tools.specifications.get(contract.status_tool)
    if spec is None or not spec.read_only:
        raise ValueError("The status tool must be registered as read_only")
    if contract.tool not in tools.specifications:
        raise ValueError("The effect tool must be registered")
    if operation.created_at > time.time():
        raise ValueError("An operation cannot be created in the future")

    attempts = checks = 0
    transport, status, reason = "not_attempted", "unknown", "no_evidence"
    evidence = {}

    def retry_window_open():
        age = time.time() - operation.created_at
        return 0 <= age < contract.idempotency_window_seconds

    async def send():
        nonlocal attempts, transport
        attempts += 1
        transport = "unknown"
        try:
            await tools.call(
                contract.tool, **dict(operation.arguments), **{contract.operation_key: operation.id}
            )
            transport = "returned"
        except Exception as exc:
            transport = "timeout" if public_tool_error(exc)["error"] == "tool_timeout" else "error"

    try:
        async with asyncio.timeout(policy.timeout_seconds):
            # Expired IDs can still be checked, but must never be submitted anew.
            if not resume and (contract.idempotency_window_seconds == 0 or retry_window_open()):
                await send()
            for index in range(policy.max_checks):
                checks += 1
                try:
                    receipt = await tools.call(
                        contract.status_tool, **{contract.operation_key: operation.id}
                    )
                except Exception:
                    receipt = None
                    status, reason = "unknown", "verification_unavailable"
                    evidence = {}
                else:
                    status, reason = _assess(receipt, contract, operation)
                    # Only evidence from the explicit status hook, never the write response.
                    evidence = copy.deepcopy(receipt) if isinstance(receipt, dict) else {}
                if status in {
                    "confirmed_success",
                    "confirmed_failure",
                    "partial",
                    "contract_violation",
                }:
                    break
                remaining_checks = index + 1 < policy.max_checks
                if (
                    reason == "not_found"
                    and remaining_checks
                    and attempts < policy.max_attempts
                    and retry_window_open()
                ):
                    await send()
                elif remaining_checks:
                    await asyncio.sleep(policy.poll_interval_seconds)
    except TimeoutError:
        # A deadline may interrupt an effect or a read: neither establishes the outcome.
        status, reason, evidence = "unknown", "verification_deadline", {}
    action = (
        "continue"
        if status == "confirmed_success"
        else (
            "stop"
            if status in {"confirmed_failure", "partial", "contract_violation"}
            else "escalate"
        )
    )
    return ToolOutcome(operation.id, transport, status, action, reason, attempts, checks, evidence)
