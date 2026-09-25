import asyncio
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

from agent_action_evals import AgentResult, FaultPlan, Scenario, StateEquals, run_scenario
from agent_action_evals.faults import with_fault_matrix
from agent_action_evals.reporting import explain, text_report

ROOT = Path(__file__).resolve().parents[1]


def test_readable_report_redacts_payloads_and_preserves_unknown_attribution():
    class Driver:
        async def run(self, user_input, tools, context):
            await tools.call("read", token="argument-secret")
            return AgentResult("output-secret", {"secret": "metadata-secret"})

    scenario = Scenario(
        "redaction",
        {"token": "state-secret"},
        "input-secret",
        Driver(),
        {"read": lambda w, a: {"token": "result-secret"}},
        (StateEquals("token", "expected-secret"),),
    )
    result = asyncio.run(run_scenario(scenario))
    report = text_report([result], ROOT / "synthetic.py")
    for marker in (
        "argument-secret",
        "output-secret",
        "metadata-secret",
        "state-secret",
        "input-secret",
        "result-secret",
        "expected-secret",
    ):
        assert marker not in report
    assert "no event attributed; unavailable" in report
    assert "Payloads redacted" in report
    full = explain(result, ROOT / "synthetic.py", True)
    for marker in (
        "argument-secret",
        "output-secret",
        "result-secret",
        "expected-secret",
        "state-secret",
    ):
        assert marker in full


def test_readable_report_marks_unexercised_fault_without_inventing_an_effect():
    class Driver:
        async def run(self, *args):
            return AgentResult("done")

    base = Scenario(
        "coverage", {"n": 0}, "go", Driver(), {"read": lambda w, a: 0}, (StateEquals("n", 0),)
    )
    scenario = with_fault_matrix(base, FaultPlan("read", ("timeout_before",)))
    result = asyncio.run(run_scenario(scenario, variant_id=scenario.variants[0].id))
    report = text_report([result], ROOT / "synthetic.py")
    assert "Fault coverage: 0/1" in report
    assert "NOT REACHED" in report
    assert "no evidence of recovery" in report
    assert "no event attributed; unavailable" in report


def test_cli_explains_duplicate_effect_and_rerun_reproduces_it(tmp_path):
    # Paths containing spaces exercise command quoting on supported platforms.
    source = tmp_path / "refund cases.py"
    source.write_text(
        """
from agent_action_evals import AgentResult, FaultPlan, Scenario, StateEquals, ToolResponseLost, with_fault_matrix
class Driver:
    async def run(self, user_input, tools, context):
        try:
            await tools.call("refund")
        except ToolResponseLost:
            await tools.call("refund")
        return AgentResult("done")
def refund(world, arguments):
    world.set("refund_count", world.get("refund_count") + 1)
    return "sensitive-result"
SCENARIOS = (with_fault_matrix(
    Scenario("refund", {"refund_count": 0}, "go", Driver(), {"refund": refund},
             (StateEquals("refund_count", 1),)),
    FaultPlan("refund", ("response_lost_after_commit",)),
),)
""",
        encoding="utf-8",
    )
    artifact = tmp_path / "evidence.txt"
    json_path = tmp_path / "evidence.json"
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_action_evals",
            str(source),
            "--seed",
            "42",
            "--timeout",
            "15",
            "--explain",
            "--text",
            str(artifact),
            "--json",
            str(json_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert run.returncode == 1, run.stderr
    report = artifact.read_text(encoding="utf-8")
    assert "sensitive-result" not in report + run.stdout
    assert "response lost after execution" in report
    assert "direct_transition" in report
    data = json.loads(json_path.read_text())
    failed = next(r for r in data["runs"] if r["status"] == "fail")
    violating_seq = failed["findings"][0]["event_seq"]
    assert f"{violating_seq} ! refund: executed" in report
    assert "--seed 42" in report and "--timeout 15.0" in report
    command = report.split("Rerun:\n  ", 1)[1].splitlines()[0]
    if os.name == "nt":
        # Windows CreateProcess parses the same quoting emitted by list2cmdline.
        rerun_args = subprocess.list2cmdline([sys.executable]) + command[len("python") :]
    else:
        rerun_args = [sys.executable, *shlex.split(command)[1:]]
    rerun = subprocess.run(rerun_args, cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert rerun.returncode == 1, rerun.stderr
    assert "0/1 runs passed" in rerun.stdout
    assert "direct_transition" in rerun.stdout


def test_list_cases_and_validate_do_not_invoke_driver(tmp_path):
    source = tmp_path / "cases.py"
    source.write_text(
        """
from agent_action_evals import FaultPlan, Scenario, StateEquals, with_fault_matrix
class Driver:
    async def run(self, *args):
        raise AssertionError("Driver must not run")
SCENARIOS = (with_fault_matrix(
    Scenario("sample", {"n": 0}, "go", Driver(), {"read": lambda w, a: 0}, (StateEquals("n", 0),)),
    FaultPlan("read", on_calls=(1, 2)),
),)
""",
        encoding="utf-8",
    )
    for flag in ("--list-cases", "--validate"):
        run = subprocess.run(
            [sys.executable, "-m", "agent_action_evals", str(source), flag],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert run.returncode == 0, run.stderr
        if flag == "--list-cases":
            assert len(run.stdout.splitlines()) == 5
            assert "sample/fault-read-timeout_before-call-2" in run.stdout
