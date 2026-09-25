from dataclasses import replace
from pathlib import Path

from refund_scenarios import SCENARIOS as BASE_SCENARIOS

from agent_action_evals.adapters import DockerDriver

SCENARIOS = tuple(
    replace(s, driver=DockerDriver(str(Path(__file__).with_name("sandbox_agent.py"))))
    for s in BASE_SCENARIOS
)
