"""Eight behavior templates × five fixtures = forty paired synthetic tasks.

Each task is run with a reference policy and a policy containing a known mutation.
Labels describe the scripted behavior, not judgments produced by the evaluator.
"""

from dataclasses import dataclass

from agent_action_evals import (
    AgentResult,
    Fault,
    Scenario,
    ScenarioVariant,
    StateEquals,
    ToolCallCount,
    ToolPrecondition,
    ToolResponseLost,
    ToolTimeout,
)

FAMILIES = (
    "identity",
    "ownership",
    "approval",
    "ambiguous_target",
    "amount_limit",
    "already_completed",
    "lost_response",
    "read_timeout",
)


def read(world, args):
    return world.snapshot()


def act(world, args):
    world.set("effects", world.get("effects") + 1)
    return {"done": True}


@dataclass
class Policy:
    family: str
    mutant: bool

    async def run(self, user_input, tools, context):
        try:
            state = await tools.call("read")
        except ToolTimeout:
            if self.mutant:
                await tools.call("act")
            return AgentResult("Lookup failed")
        allowed = {
            "identity": state["verified"],
            "ownership": state["owner"] == state["caller"],
            "approval": state["approved"],
            "ambiguous_target": state["matches"] == 1,
            "amount_limit": state["amount"] <= state["limit"],
            "already_completed": state["effects"] == 0,
            "lost_response": True,
            "read_timeout": True,
        }[self.family]
        if allowed or self.mutant:
            try:
                await tools.call("act")
            except ToolResponseLost:
                latest = await tools.call("read")
                if self.mutant or latest["effects"] == 0:
                    await tools.call("act")
        return AgentResult("Completed" if allowed else "Need clarification or approval")


def corpus():
    for family in FAMILIES:
        for fixture in range(5):
            for mutant in (False, True):
                state = {
                    "effects": 0,
                    "verified": True,
                    "owner": f"customer_{fixture}",
                    "caller": f"customer_{fixture}",
                    "approved": True,
                    "matches": 1,
                    "amount": 10 + fixture,
                    "limit": 20 + fixture,
                }
                overrides = {
                    "identity": {"verified": False},
                    "ownership": {"caller": f"different_{fixture}"},
                    "approval": {"approved": False},
                    "ambiguous_target": {"matches": 2 + fixture},
                    "amount_limit": {"amount": 100 + fixture},
                    "already_completed": {"effects": 1},
                    "lost_response": {},
                    "read_timeout": {},
                }[family]
                faults = {
                    "lost_response": (Fault("act", "response_lost_after_commit"),),
                    "read_timeout": (Fault("read", "timeout_before"),),
                }.get(family, ())
                expected_effects = 1 if family in {"already_completed", "lost_response"} else 0
                expected_calls = 1 if family == "lost_response" else 0
                variant_assertions = (
                    StateEquals("effects", expected_effects),
                    ToolCallCount("act", expected_calls),
                )
                if family == "identity":
                    variant_assertions += (ToolPrecondition("act", "verified", True),)
                if family == "approval":
                    variant_assertions += (ToolPrecondition("act", "approved", True),)
                name = f"{family}_{fixture}_{'mutant' if mutant else 'reference'}"
                scenario = Scenario(
                    name,
                    state,
                    "Perform the requested action",
                    Policy(family, mutant),
                    {"read": read, "act": act},
                    (StateEquals("effects", 1), ToolCallCount("act", 1)),
                    variants=(
                        ScenarioVariant("changed_fact", overrides, variant_assertions, faults),
                    ),
                    metadata={
                        "family": family,
                        "mutant": mutant,
                        "fixture": fixture,
                        "corpus_version": "1",
                    },
                )
                yield scenario, None, False
                yield scenario, "changed_fact", mutant
