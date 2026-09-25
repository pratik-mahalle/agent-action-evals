# Contributing

Install Python 3.11 or newer, then `python -m pip install -e '.[dev,langgraph]'`.
Run `ruff check src tests examples benchmarks`, `ruff format --check src tests examples benchmarks`, and `python -m pytest -q`. Run `python benchmarks/run.py --sizes 100` for a short evaluator conformance check.

Changes to runner verdicts, state effects, or lifecycle handling need regression tests demonstrating the failure they fix. Keep live model tests opt-in and record model/configuration/cost metadata with results. Container changes require the real Docker integration tests in addition to protocol tests. Public API/report changes must update the contract documentation and changelog.

Benchmark additions should state the ground-truth label independently of the evaluator and explain whether cases are new behavioral templates or parameter variations. Include safe controls. Do not call synthetic mutation results real-agent reliability results.
