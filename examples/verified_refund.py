"""Run: agent-action-evals examples/verified_refund.py --explain"""

from verification_support import VerifiedRefundAgent, make_scenarios

SCENARIOS = make_scenarios(VerifiedRefundAgent())
