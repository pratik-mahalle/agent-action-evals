import json
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]


def invoke(*args):
    return subprocess.run(
        [sys.executable, "-m", "agent_action_evals", *map(str, args)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
    )


def test_cli_reports_and_preflight(tmp_path):
    report, junit = tmp_path / "run.json", tmp_path / "run.xml"
    result = invoke(
        "examples/refund_scenarios.py",
        "--repeat",
        2,
        "--json",
        report,
        "--junit",
        junit,
        "--jsonl",
        tmp_path / "runs.jsonl",
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(report.read_text())
    assert data["schema_version"] == "1.0"
    assert data["summary"]["runs"] == 6
    assert data["summary"]["statuses"] == {"pass": 6}
    assert len(ElementTree.parse(junit).findall("testcase")) == 6
    assert len((tmp_path / "runs.jsonl").read_text().splitlines()) == 6
    assert invoke("examples/refund_scenarios.py", "--validate").returncode == 0


def test_cli_no_matching_case_is_configuration_error():
    assert invoke("examples/refund_scenarios.py", "--case", "absent").returncode == 2


def test_cli_failed_assertion_exit_code(tmp_path):
    path = tmp_path / "cases.py"
    path.write_text("""
from agent_action_evals import *
class Driver:
    async def run(self, user_input, tools, context):
        return AgentResult("done")
SCENARIOS = (Scenario("failure", {"n": 0}, "act", Driver(), {}, (StateEquals("n", 1),)),)
""")
    assert invoke(path).returncode == 1
