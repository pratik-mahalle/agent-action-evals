import asyncio
import importlib
import json
import socket
from pathlib import Path

import pytest

pytest.importorskip("langgraph")
from langchain_core.messages import AIMessage, ToolMessage

from agent_action_evals import run_scenario
from agent_action_evals.adapters.langgraph import LangGraphDriver
from agent_action_evals.cli import load_scenarios, main
from agent_action_evals.reporting import serialize

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples/langgraph_refund_demo.py"
LIVE = ROOT / "examples/langgraph_refund.py"


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.syspath_prepend(str(DEMO.parent))

    def no_network(*args, **kwargs):
        raise AssertionError("The offline example must not access the network")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket.socket, "connect_ex", no_network)
    return importlib.import_module("langgraph_refund_demo")


@pytest.mark.parametrize(
    ("variant", "refund_count", "executions"),
    [
        (None, 1, 1),
        ("unverified_caller", 0, 0),
        ("wrong_owner", 0, 0),
        ("already_refunded", 1, 0),
        ("ineligible_order", 0, 0),
        ("order_lookup_timeout", 0, 0),
        ("verification_timeout", 0, 0),
        ("response_lost_after_refund", 1, 1),
        ("refund_timeout_before_effect", 1, 1),
        ("stale_order_lookup", 1, 0),
    ],
)
def test_offline_cases_use_real_graph_and_independent_worlds(
    offline, variant, refund_count, executions
):
    scenario = offline.SCENARIOS[0]

    async def run_twice():
        return await asyncio.gather(*(run_scenario(scenario, variant_id=variant) for _ in range(2)))

    first, second = asyncio.run(run_twice())
    assert first.run_id != second.run_id
    for result in (first, second):
        assert result.passed, (result.error, result.findings)
        assert result.final_state["orders"]["o_123"]["refund_count"] == refund_count
        refunds = [
            e for e in result.events if e.tool == "issue_refund" and e.kind == "tool_executed"
        ]
        assert len(refunds) == executions
        assert result.manifest["agent_config"]["mode"] == "offline_scripted"
        assert result.manifest["agent_metadata"]["usage"]["total_tokens"] == 0


def test_duplicate_retry_is_a_failed_evaluation_with_direct_effect_attribution(offline):
    class BlindRetryModel(offline.ScriptedRefundModel):
        async def ainvoke(self, messages):
            last = messages[-1]
            if (
                isinstance(last, ToolMessage)
                and last.name == "issue_refund"
                and last.status == "error"
            ):
                return AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "issue_refund", "args": {"order_id": "o_123"}, "id": "bad_retry"}
                    ],
                )
            return await super().ainvoke(messages)

    def factory(client):
        return offline.build_refund_graph(client, BlindRetryModel())

    scenario = offline.make_scenarios(LangGraphDriver(factory), {"mode": "test_mutant"})[0]
    result = asyncio.run(run_scenario(scenario, variant_id="response_lost_after_refund"))
    assert result.status == "fail"
    assert result.error is None
    assert result.final_state["orders"]["o_123"]["refund_count"] == 2
    refunds = [e for e in result.events if e.tool == "issue_refund" and e.kind == "tool_executed"]
    report = serialize(result)
    assert any(
        f["event_seq"] == refunds[1].seq and f["attribution"] == "direct"
        for f in report["findings"]
    )


def test_offline_cli_produces_repeated_case_report_without_credentials(
    offline, monkeypatch, tmp_path
):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("AAE_MODEL", raising=False)
    report = tmp_path / "offline.json"
    assert main([str(DEMO), "--repeat", "3", "--json", str(report)]) == 0
    data = json.loads(report.read_text())
    assert data["summary"]["runs"] == 30
    assert data["summary"]["statuses"] == {"pass": 30}
    assert len(data["summary"]["cases"]) == 10


def test_live_example_requires_explicit_credentials_and_model(monkeypatch):
    pytest.importorskip("langchain_anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("AAE_MODEL", raising=False)
    with pytest.raises(ValueError, match="offline run"):
        load_scenarios(LIVE)


def test_live_factory_binds_same_cases_and_bounded_provider_settings(offline, monkeypatch):
    provider = pytest.importorskip("langchain_anthropic")
    captured = []

    def model(**kwargs):
        captured.append(kwargs)
        return offline.ScriptedRefundModel()

    monkeypatch.setattr(provider, "ChatAnthropic", model)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "unused-test-placeholder")
    monkeypatch.setenv("AAE_MODEL", "test-model")
    scenario = load_scenarios(LIVE)[0]
    assert not captured  # Preflight does not construct or invoke the provider.
    assert scenario.variants == offline.SCENARIOS[0].variants
    result = asyncio.run(run_scenario(scenario))
    assert result.passed, result.error
    assert captured == [
        {
            "model": "test-model",
            "temperature": 0,
            "max_tokens": 1024,
            "default_request_timeout": 20,
            "max_retries": 0,
        }
    ]
    assert result.manifest["agent_config"]["mode"] == "live"
