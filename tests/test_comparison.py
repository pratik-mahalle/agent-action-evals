import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_pinned_sdk_comparison_and_independent_execution_parity(tmp_path):
    source = os.environ.get("AAE_FAILPROOF_SOURCE")
    if not source:
        pytest.skip("Set AAE_FAILPROOF_SOURCE to the pinned upstream checkout")
    path = tmp_path / "comparison.json"
    run = subprocess.run(
        [
            sys.executable,
            "benchmarks/comparison/run.py",
            "--failproof-root",
            source,
            "--output",
            str(path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    report = json.loads(path.read_text())
    assert len(report["records"]) == 12
    assert report["summary"]["behavior_matches"] == 12
    for record in report["records"]:
        expected = "fail" if record["expected_unsafe"] else "pass"
        assert record["aae"]["status"] == record["failproof"]["status"] == expected
        if record["expected_unsafe"]:
            assert record["aae"]["first_effect_localized"]
            assert record["failproof"]["first_effect_localized"]
    assert report["missing_evidence_control"]["status"] == "error"
