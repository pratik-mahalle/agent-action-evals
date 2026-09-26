"""Offline checks of the live harness; these are not live model quality results."""

import asyncio
import importlib
import json
from pathlib import Path

import pytest

from agent_action_evals import Operation, RecoveryPolicy, ToolContract, execute_verified
from agent_action_evals.verification import request_fingerprint

httpx = pytest.importorskip("httpx")


@pytest.fixture
def live(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "benchmarks"))
    return importlib.import_module("live_jev")


@pytest.mark.parametrize(
    "case,status,writes",
    [
        ("normal", "confirmed_success", 1),
        ("timeout_before", "confirmed_success", 1),
        ("response_lost", "confirmed_success", 1),
        ("stale_status_after_commit", "confirmed_success", 1),
        ("false_success", "unknown", 2),
        ("verification_unavailable", "unknown", 1),
    ],
)
def test_faults_wrap_http_commit_and_independent_receipt(live, case, status, writes):
    operation = Operation("refund_test", {"order_id": "order_test", "amount_cents": 500})
    ledger, requests = {}, []

    def handler(request):
        assert request.headers["authorization"] == "Bearer service-only"
        requests.append(request.url.path)
        if request.url.path == "/refund":
            args = json.loads(request.content)
            assert args["operation_id"] == operation.id
            if case != "false_success":
                ledger.setdefault(operation.id, args)
            return httpx.Response(200, json={"status": "succeeded"})
        assert request.url.path == "/status"
        receipt = {
            "operation_id": operation.id,
            "tool": "issue_refund",
            "authoritative": True,
            "status": "not_found",
        }
        if ledger:
            receipt.update(
                status="succeeded",
                receipt_id=operation.id,
                request_sha256=request_fingerprint("issue_refund", operation.arguments),
                effect={**operation.arguments, "refund_count": len(ledger)},
            )
        return httpx.Response(200, json=receipt)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            service = live.LiveService(
                http, "https://service.test", "service-only", operation, case
            )
            result = await execute_verified(
                service.client(),
                ToolContract(
                    "issue_refund",
                    "get_refund_status",
                    effect_equals={"refund_count": 1},
                    effect_matches={"order_id": "order_id", "amount_cents": "amount_cents"},
                    idempotency_window_seconds=3600,
                ),
                operation,
                RecoveryPolicy(poll_interval_seconds=0),
            )
            assert result.status == status
            assert requests.count("/refund") == writes
            assert len(ledger) == (case != "false_success")
            if case in {"response_lost", "stale_status_after_commit"}:
                assert result.transport == "timeout"
                assert result.attempts == 1
                assert service.trace[0]["transport"] == "timeout"
                assert requests == ["/refund", "/status"]
            if case == "timeout_before":
                assert result.attempts == 2
                assert requests[0] == "/status"

    asyncio.run(run())


def test_live_trial_blinds_model_and_keeps_credentials_scoped(live, tmp_path):
    ledger, sent_states = {}, []

    def handler(request):
        if request.url.host == "api.typesafe.ai":
            assert request.url.path == "/v1/systemone"
            assert request.headers["authorization"] == "Bearer model-only"
            body = json.loads(request.content)
            state = body["state"]
            sent_states.append(state)
            assert set(state) == {"objective", "operation", "observations", "remaining_decisions"}
            assert "response_lost_after_commit" not in json.dumps(state)
            action = "issue_refund" if not state["observations"] else "finish_success"
            return httpx.Response(
                200,
                json={
                    "model": "test-model",
                    "answers": {"next_action": {"choice": action}},
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                },
            )
        assert request.headers["authorization"] == "Bearer service-only"
        if request.url.path == "/fixture":
            return httpx.Response(200, json={})
        if request.url.path == "/refund":
            args = json.loads(request.content)
            ledger[args["operation_id"]] = args
            return httpx.Response(200, json={"status": "succeeded"})
        assert request.url.path == "/oracle"
        return httpx.Response(200, json={"refund_count": len(ledger), "refunded_cents": 500})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            record = await live.run_trial(
                http,
                {"endpoint": "https://service.test", "token": "service-only"},
                "model-only",
                "test-model",
                "response_lost",
                "direct",
                tmp_path,
            )
        assert record["fault_coverage"] and len(ledger) == 1
        assert record["unverified_success"] and not record["passed"]
        assert not record["false_success"]  # A lucky correct guess is still unverified.
        assert sent_states[1]["observations"][0]["result"] == {
            "transport": "timeout",
            "outcome": "unknown",
        }
        assert "model-only" not in json.dumps(record)
        assert "service-only" not in json.dumps(record)
        saved = json.loads(next(tmp_path.glob("*.json")).read_text())
        assert saved["id"] == record["operation_id"]
        assert saved["created_at"] > 0

    asyncio.run(run())
