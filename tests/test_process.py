import asyncio
import os
import sys

import pytest

from agent_action_evals import Scenario, StateEquals, run_scenario
from agent_action_evals.adapters import DockerDriver, ProcessDriver


def scenario(driver):
    return Scenario(
        "process",
        {"n": 0},
        "test",
        driver,
        {"increment": lambda world, args: world.set("n", 1)},
        (StateEquals("n", 1),),
    )


def script(tmp_path, body):
    path = tmp_path / "agent.py"
    path.write_text("import sys, json, os, time\nstart=json.loads(sys.stdin.readline())\n" + body)
    return path


def test_process_routes_tools_without_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("AAE_TEST_SECRET", "should-never-reach-agent")
    path = script(
        tmp_path,
        """
assert "AAE_TEST_SECRET" not in os.environ
assert "initial_state" not in start
print(json.dumps({"id": 1, "method": "tool.call", "name": "increment", "arguments": {}}), flush=True)
response=json.loads(sys.stdin.readline())
assert "result" in response
print(json.dumps({"method": "result", "text": "done"}), flush=True)
""",
    )
    result = asyncio.run(run_scenario(scenario(ProcessDriver((sys.executable, str(path))))))
    assert result.passed, result.error
    assert result.manifest["isolation"] == "trusted_subprocess"


def test_process_timeout_kills_agent_before_later_write(tmp_path):
    marker = tmp_path / "must-not-exist"
    path = script(tmp_path, f'time.sleep(1)\nopen({str(marker)!r}, "w").write("bad")\n')

    async def execute():
        result = await run_scenario(
            scenario(ProcessDriver((sys.executable, str(path)))), timeout_seconds=0.05
        )
        await asyncio.sleep(1.1)
        return result

    result = asyncio.run(execute())
    assert result.status == "timeout"
    assert not marker.exists()


@pytest.mark.parametrize(
    "body",
    [
        'print("not JSON", flush=True)\n',
        'print(json.dumps({"method": "world.get"}), flush=True)\n',
        'print(json.dumps({"method": "result", "text": 123}), flush=True)\n',
    ],
)
def test_malformed_protocol_is_error(tmp_path, body):
    path = script(tmp_path, body)
    result = asyncio.run(run_scenario(scenario(ProcessDriver((sys.executable, str(path))))))
    assert result.status == "error"


def test_stderr_flood_does_not_deadlock(tmp_path):
    path = script(
        tmp_path,
        """
sys.stderr.write("x" * 2000000)
print(json.dumps({"method": "result", "text": "done"}), flush=True)
""",
    )
    result = asyncio.run(run_scenario(scenario(ProcessDriver((sys.executable, str(path))))))
    assert result.status == "fail"  # state assertion fails; protocol completes
    assert result.error is None


@pytest.mark.skipif(
    os.environ.get("AAE_DOCKER_TESTS") != "1",
    reason="requires a running Docker daemon and local image",
)
def test_docker_enforces_offline_filesystem_and_tool_boundary(tmp_path):
    path = tmp_path / "probe.py"
    path.write_text("""
import os, socket
from remote import RemoteTools
client = RemoteTools()
assert "AAE_TEST_SECRET" not in os.environ
try:
    socket.create_connection(("1.1.1.1", 443), timeout=1)
except OSError:
    pass
else:
    raise RuntimeError("network bypass")
try:
    open("/root-write", "w").write("bad")
except OSError:
    pass
else:
    raise RuntimeError("root filesystem writable")
client.call("increment")
client.finish("isolated")
""")
    result = asyncio.run(run_scenario(scenario(DockerDriver(str(path))), timeout_seconds=30))
    assert result.passed, result.error
    assert result.manifest["agent_metadata"]["image_id"].startswith("sha256:")


@pytest.mark.skipif(
    os.environ.get("AAE_DOCKER_TESTS") != "1",
    reason="requires a running Docker daemon and local image",
)
def test_docker_timeout_removes_container(tmp_path):
    import subprocess

    path = tmp_path / "loop.py"
    path.write_text("while True: pass\n")
    result = asyncio.run(run_scenario(scenario(DockerDriver(str(path))), timeout_seconds=5))
    assert result.status == "timeout", result.error
    remaining = subprocess.check_output(
        ["docker", "ps", "-aq", "--filter", f"name=agent-eval-{result.run_id}"], text=True
    )
    assert not remaining.strip()
