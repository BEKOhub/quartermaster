"""End-to-end pipeline tests in mock mode: happy path, escalation, over-budget,
the review->repair loop, the injection scanner, and structural escalation.
"""
from quartermaster.audit import AuditLog
from quartermaster.broker.broker import SecretsBroker
from quartermaster.broker.connectors.mock import MockGitHubConnector, MockJiraConnector
from quartermaster.budget import BudgetLedger
from quartermaster.claude_mock import mock_brain
from quartermaster.claude_runner import ClaudeRunner
from quartermaster.models import Outcome, Status, Ticket
from quartermaster.notify import Notifier
from quartermaster.observability import Observability
from quartermaster.pipeline import Pipeline
from quartermaster.repomap import RepoMap
from quartermaster.worktree import WorktreeManager


def make_pipeline(settings, jira=None):
    jira = jira or MockJiraConnector()
    audit = AuditLog(settings.audit_log_path)
    broker = SecretsBroker(settings, audit, jira, MockGitHubConnector())
    budget = BudgetLedger(settings.budget_db_path,
                          per_ticket_usd=settings.budget_per_ticket_usd,
                          monthly_usd=settings.budget_monthly_usd)
    obs = Observability(settings.runs_db_path)
    runner = ClaudeRunner(settings, mock_provider=mock_brain)
    pipe = Pipeline(settings, broker, budget, runner, WorktreeManager(settings), obs,
                    repomap=RepoMap(settings.repo_path), notifier=Notifier(mock_mode=True))
    return pipe, broker, budget, jira, obs


def test_happy_path_opens_pr(settings):
    pipe, broker, budget, jira, obs = make_pipeline(settings)
    result = pipe.run(jira.get_ticket("DEMO-101"))
    assert result.outcome == Outcome.PR_OPENED
    assert result.pr_url
    assert jira.get_ticket("DEMO-101").status == Status.IN_REVIEW
    assert budget.ticket_total("DEMO-101") > 0
    # observability recorded a timeline (plan, implement, review x3, scan, pr...)
    assert len(obs.timeline("DEMO-101")) >= 5


def test_arch_ticket_escalates(settings):
    pipe, broker, budget, jira, obs = make_pipeline(settings)
    result = pipe.run(jira.get_ticket("DEMO-103"))
    assert result.outcome == Outcome.NEEDS_DECISION
    assert jira.get_ticket("DEMO-103").status == Status.NEEDS_DECISION
    assert any("Architecture decision needed" in c for c in jira.comments["DEMO-103"])


def test_over_budget_blocks(settings):
    pipe, broker, budget, jira, obs = make_pipeline(settings)
    budget.record("DEMO-102", "implement", 99.0)
    result = pipe.run(jira.get_ticket("DEMO-102"))
    assert result.outcome == Outcome.BLOCKED
    assert jira.get_ticket("DEMO-102").status == Status.BLOCKED


def test_review_repair_loop_then_pr(settings):
    """DEMO-104 (reviewfail): reviewers reject round 0, accept after one repair."""
    pipe, broker, budget, jira, obs = make_pipeline(settings)
    result = pipe.run(jira.get_ticket("DEMO-104"))
    assert result.outcome == Outcome.PR_OPENED
    # a repair round happened
    assert any("Repair round" in c for c in jira.comments["DEMO-104"])
    stages = [s["stage"] for s in obs.timeline("DEMO-104")]
    assert stages.count("implement") >= 2  # original + repair


def test_injection_blocked_by_scanner(settings):
    """DEMO-105 (inject): poisoned diff is caught before any PR."""
    pipe, broker, budget, jira, obs = make_pipeline(settings)
    result = pipe.run(jira.get_ticket("DEMO-105"))
    assert result.outcome == Outcome.BLOCKED
    assert jira.get_ticket("DEMO-105").status == Status.BLOCKED
    assert any("security scan" in c for c in jira.comments["DEMO-105"])


def test_structural_escalates(settings):
    """DEMO-106 (structural): reviewer flags a structural problem -> escalate."""
    pipe, broker, budget, jira, obs = make_pipeline(settings)
    result = pipe.run(jira.get_ticket("DEMO-106"))
    assert result.outcome == Outcome.NEEDS_DECISION
    assert jira.get_ticket("DEMO-106").status == Status.NEEDS_DECISION
