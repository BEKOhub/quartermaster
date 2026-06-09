"""The eval harness itself must pass — all golden scenarios hit their expected
outcome. This doubles as a regression gate for the whole pipeline.
"""
from quartermaster.evals import run_evals


def test_all_scenarios_pass():
    passed, total, cost, rows = run_evals()
    failures = [r for r in rows if not r["ok"]]
    assert not failures, f"eval scenarios failed: {failures}"
    assert passed == total
    assert cost > 0  # work actually happened
