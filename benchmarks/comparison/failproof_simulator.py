"""Custom fixture runner and state telemetry supplied by the benchmark author.

Failproof's SDK evaluates finished sessions. This simulator supplies the test
world, deterministic faults, and audit snapshots independently of AAE.
"""

import copy
from collections import Counter

from cases import HANDLERS, ResponseLost, ScriptedPolicy


class DictWorld:
    """Standalone simulator storage. No dependency on either evaluation framework."""

    def __init__(self, state):
        self.state = copy.deepcopy(state)

    def get(self, path):
        value = self.state
        for part in path.split("."):
            value = value[part]
        return copy.deepcopy(value)

    def set(self, path, value):
        target = self.state
        *parts, leaf = path.split(".")
        for part in parts:
            target = target[part]
        target[leaf] = copy.deepcopy(value)

    def snapshot(self):
        return copy.deepcopy(self.state)


class Simulator:
    def __init__(self, case):
        self.case = case
        self.world = DictWorld(case.state())
        self.calls = Counter()
        self.events = []
        self.record(
            "agent_start",
            initial_state=self.world.snapshot(),
            contract={
                "refunds": case.expected_refunds,
                "executions": case.expected_executions,
            },
        )

    def record(self, kind, **payload):
        self.events.append(
            {"id": f"event_{len(self.events) + 1}", "type": kind, "payload": copy.deepcopy(payload)}
        )

    async def call(self, name):
        self.calls[name] += 1
        call_id = f"{name}_{self.calls[name]}"
        self.record("tool_use", tool_name=name, tool_call_id=call_id, input={})
        before = self.world.snapshot()
        if name == "get_order" and self.calls[name] == 1 and self.case.fault == "stale_read":
            result = self.case.stale_order()
            executed = False
        else:
            result = HANDLERS[name](self.world, {})
            executed = True
        lost = (
            name == "issue_refund"
            and self.calls[name] == 1
            and self.case.fault == "response_lost_after_commit"
        )
        self.record(
            "tool_result",
            tool_name=name,
            tool_call_id=call_id,
            output=None if lost else result,
            error="response_lost" if lost else None,
            audit={"before": before, "after": self.world.snapshot(), "executed": executed},
        )
        if lost:
            raise ResponseLost
        return copy.deepcopy(result)

    async def run(self):
        output = await ScriptedPolicy(self.case.family, self.case.mutant).run(self)
        self.record("agent_end", output=output, final_state=self.world.snapshot())
        return self.events
