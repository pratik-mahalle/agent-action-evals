import asyncio
import importlib
import json
import time
from dataclasses import replace
from pathlib import Path

import pytest

from agent_action_evals import (
    Fault,
    Operation,
    RecoveryPolicy,
    ToolPort,
    ToolResponseLost,
    ToolTimeout,
    World,
    execute_verified,
    public_tool_error,
    run_scenario,
)
from agent_action_evals.reporting import serialize


@pytest.fixture
def support(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "examples"))
    return importlib.import_module("verification_support")


@pytest.mark.parametrize(
    "case",
    [
        None,
        "timeout_before",
        "response_lost",
        "false_success",
        "stale_status_after_commit",
        "pending_then_visible",
        "verification_unavailable",
        "pending",
        "partial",
        "wrong_amount",
        "failed",
        "wrong_operation_receipt",
    ],
)
def test_verified_refund_cases(support, case):
    scenario = support.make_scenarios(support.VerifiedRefundAgent())[0]
    result = asyncio.run(run_scenario(scenario, variant_id=case))
    assert result.passed, (result.error, result.findings)
    outcome = json.loads(result.output)
    assert outcome["attempts"] <= 2 and outcome["checks"] <= 4
    if case == "stale_status_after_commit":
        assert outcome["attempts"] == 1
        assert outcome["checks"] == 2
    if case == "timeout_before":
        attempts = [
            e for e in result.events if e.kind == "tool_attempt" and e.tool == "issue_refund"
        ]
        assert len(attempts) == 2
        assert attempts[0].details["arguments"] == attempts[1].details["arguments"]
    redacted = json.dumps(serialize(result))
    assert "receipt-refund-o_123" not in redacted


def test_real_langgraph_uses_same_receipts_and_faults(support):
    pytest.importorskip("langgraph")
    demo = importlib.import_module("langgraph_verified_refund")

    async def run():
        scenario = demo.SCENARIOS[0]
        results = await asyncio.gather(
            *(
                run_scenario(scenario, variant_id=case)
                for case in [None, *[v.id for v in scenario.variants]]
            )
        )
        assert all(r.passed for r in results), [
            (r.case_id, r.error, r.findings) for r in results if not r.passed
        ]
        assert all(r.manifest["agent_metadata"]["usage"]["total_tokens"] == 0 for r in results)

    asyncio.run(run())


def test_timeouts_expose_identical_agent_observations(support):
    assert public_tool_error(ToolTimeout("internal")) == public_tool_error(
        ToolResponseLost("secret")
    )
    pytest.importorskip("langgraph")
    graph = importlib.import_module("langgraph_support")
    assert graph.tool_error(ToolTimeout("x")) == graph.tool_error(ToolResponseLost("x"))
    assert "ToolResponseLost" not in graph.tool_error(ToolResponseLost("x"))


def run_client(
    support, faults=(), *, contract=None, operation=None, policy=None, resume=False, handlers=None
):
    scenario = support.make_scenarios(support.VerifiedRefundAgent())[0]
    world = World(scenario.initial_state)
    port = ToolPort(
        world, handlers or scenario.handlers, faults, specifications=scenario.specifications
    )
    try:
        result = asyncio.run(
            execute_verified(
                port.client(),
                contract or support.CONTRACT,
                operation or Operation(support.OPERATION_ID, support.ARGUMENTS),
                policy or support.POLICY,
                resume=resume,
            )
        )
        return result, world.snapshot(), port.events
    finally:
        port.seal()
        world.close()


def test_absence_without_server_idempotency_never_retries(support):
    contract = replace(support.CONTRACT, idempotency_window_seconds=0)
    outcome, state, _ = run_client(
        support, (Fault("issue_refund", "timeout_before"),), contract=contract
    )
    assert outcome.status == "unknown" and outcome.next_action == "escalate"
    assert outcome.attempts == 1 and state["order"]["refund_count"] == 0


@pytest.mark.parametrize("resume", [False, True])
def test_expired_operation_is_checked_but_never_submitted(support, resume):
    operation = Operation(support.OPERATION_ID, support.ARGUMENTS, created_at=time.time() - 90000)
    outcome, state, events = run_client(support, operation=operation, resume=resume)
    assert outcome.status == "unknown" and outcome.attempts == 0
    assert state["order"]["refund_count"] == 0
    assert all(e.tool != "issue_refund" for e in events)


def test_no_retry_without_a_remaining_verification_budget(support):
    outcome, state, _ = run_client(
        support, (Fault("issue_refund", "timeout_before"),), policy=RecoveryPolicy(max_checks=1)
    )
    assert outcome.status == "unknown"
    assert outcome.attempts == 1 and outcome.checks == 1
    assert state["order"]["refund_count"] == 0


@pytest.mark.parametrize(
    "patch",
    [
        {"operation_id": "different"},
        {"request_sha256": "different"},
        {"tool": "other"},
        {"authoritative": False},
        {"authoritative": 1},
        {"receipt_id": ""},
        {"effect": {}},
    ],
)
def test_wrong_receipt_never_confirms_success(support, patch):
    forged = {**support.receipt(), **patch}
    faults = tuple(
        Fault("get_refund_status", "stale_read", on_call=i, value=forged) for i in range(1, 5)
    )
    outcome, state, _ = run_client(support, faults)
    assert outcome.status in {"unknown", "contract_violation"}
    assert outcome.next_action != "continue"
    assert outcome.attempts == 1 and state["order"]["refund_count"] == 1


def test_deadline_after_commit_retains_unknown_and_records_effect(support):
    async def delayed(world, arguments):
        support.issue_refund(world, arguments)
        await asyncio.sleep(10)

    outcome, state, events = run_client(
        support,
        handlers={
            "issue_refund": delayed,
            "get_refund_status": support.get_refund_status,
        },
        policy=RecoveryPolicy(timeout_seconds=0.01),
    )
    assert outcome.status == "unknown" and outcome.reason == "verification_deadline"
    assert outcome.attempts == 1
    assert state["order"]["refund_count"] == 1
    effects = [e for e in events if e.kind == "tool_executed"]
    assert effects[0].details["status"] == "cancelled"
    assert "order.refund_count" in effects[0].details["changes"]


def test_external_cancellation_propagates(support):
    async def run():
        started = asyncio.Event()

        async def handler(world, arguments):
            support.issue_refund(world, arguments)
            started.set()
            await asyncio.sleep(10)

        scenario = support.make_scenarios(support.VerifiedRefundAgent())[0]
        world = World(scenario.initial_state)
        port = ToolPort(
            world,
            {**scenario.handlers, "issue_refund": handler},
            specifications=scenario.specifications,
        )
        try:
            task = asyncio.create_task(
                execute_verified(
                    port.client(),
                    support.CONTRACT,
                    Operation(support.OPERATION_ID, support.ARGUMENTS),
                )
            )
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert world.get("order.refund_count") == 1
        finally:
            port.seal()
            world.close()

    asyncio.run(run())


def test_resume_reuses_receipt_and_rejects_changed_payload(support):
    async def run():
        scenario = support.make_scenarios(support.VerifiedRefundAgent())[0]
        world = World(scenario.initial_state)
        port = ToolPort(world, scenario.handlers, specifications=scenario.specifications)
        operation = Operation(support.OPERATION_ID, support.ARGUMENTS)
        try:
            first = await execute_verified(
                port.client(), support.CONTRACT, operation, support.POLICY
            )
            second = await execute_verified(
                port.client(), support.CONTRACT, operation, support.POLICY, resume=True
            )
            assert first.status == second.status == "confirmed_success"
            assert second.attempts == 0 and world.get("order.refund_count") == 1
            altered = Operation(
                operation.id, {**support.ARGUMENTS, "amount_cents": 900}, operation.created_at
            )
            third = await execute_verified(
                port.client(), support.CONTRACT, altered, support.POLICY, resume=True
            )
            assert third.status == "unknown" and third.attempts == 0
            assert world.get("order.refunded_cents") == 500
        finally:
            port.seal()
            world.close()

    asyncio.run(run())


def test_baseline_failures_are_detected_by_hidden_state(support):
    scenario = support.make_scenarios(support.ResponseTrustingAgent(), coverage=False)[0]
    for case, rule in [
        ("false_success", "success claim matches committed effect"),
        ("wrong_amount", "success claim matches committed effect"),
        ("stale_status_after_commit", "state[order.refund_count]"),
    ]:
        result = asyncio.run(run_scenario(scenario, variant_id=case))
        assert result.status == "fail", result.error
        assert any(f.rule == rule for f in result.findings)


def test_invalid_contract_rejected_before_effect(support):
    contract = replace(support.CONTRACT, effect_matches={"missing": "absent_argument"})
    with pytest.raises(ValueError, match="missing operation argument"):
        run_client(support, contract=contract)
    with pytest.raises(ValueError, match="two definitions"):
        replace(support.CONTRACT, effect_matches={"refund_count": "amount_cents"})
    for kwargs in [
        {"max_attempts": 0},
        {"max_checks": True},
        {"timeout_seconds": float("inf")},
        {"poll_interval_seconds": -1},
    ]:
        with pytest.raises(ValueError):
            RecoveryPolicy(**kwargs)


def test_concurrent_same_operation_is_deduplicated_by_service(support):
    async def run():
        scenario = support.make_scenarios(support.VerifiedRefundAgent())[0]
        world = World(scenario.initial_state)
        port = ToolPort(world, scenario.handlers, specifications=scenario.specifications)
        operation = Operation(support.OPERATION_ID, support.ARGUMENTS)
        try:
            outcomes = await asyncio.gather(
                *(
                    execute_verified(port.client(), support.CONTRACT, operation, support.POLICY)
                    for _ in range(2)
                )
            )
            assert all(o.status == "confirmed_success" for o in outcomes)
            assert world.get("order.refund_count") == 1
            assert world.get("order.refunded_cents") == 500
            assert (
                sum(e.kind == "tool_attempt" and e.tool == "issue_refund" for e in port.events) == 2
            )
        finally:
            port.seal()
            world.close()

    asyncio.run(run())
