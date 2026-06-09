"""The dashboard state builder produces the board + indicators the UI expects."""
from quartermaster.audit import AuditLog
from quartermaster.broker.broker import SecretsBroker
from quartermaster.broker.connectors.mock import MockGitHubConnector, MockJiraConnector
from quartermaster.budget import BudgetLedger
from quartermaster.dashboard import DashboardState
from quartermaster.notify import Notifier
from quartermaster.observability import Observability


def make_state(settings, fake_queue):
    jira = MockJiraConnector()
    broker = SecretsBroker(settings, AuditLog(settings.audit_log_path),
                           jira, MockGitHubConnector())
    budget = BudgetLedger(settings.budget_db_path,
                          per_ticket_usd=settings.budget_per_ticket_usd,
                          monthly_usd=settings.budget_monthly_usd)
    budget.record("DEMO-101", "implement", 0.45)
    obs = Observability(settings.runs_db_path)
    state = DashboardState(settings, broker, fake_queue, budget, obs, Notifier(mock_mode=True))
    return state, jira


def test_state_has_all_columns(settings, fake_queue):
    state, _ = make_state(settings, fake_queue)
    s = state.build()
    labels = [c["label"] for c in s["columns"]]
    assert labels == ["To Do", "Queued", "In Progress", "Needs Decision",
                      "In Review", "Blocked", "Done"]


def test_state_places_tickets_and_costs(settings, fake_queue):
    state, _ = make_state(settings, fake_queue)
    s = state.build()
    todo = next(c for c in s["columns"] if c["key"] == "todo")
    keys = {t["key"] for t in todo["tickets"]}
    assert {"DEMO-101", "DEMO-102", "DEMO-103"} <= keys
    sci101 = next(t for t in todo["tickets"] if t["key"] == "DEMO-101")
    assert sci101["cost"] == 0.45


def test_state_indicators(settings, fake_queue):
    state, _ = make_state(settings, fake_queue)
    s = state.build()
    ind = s["indicators"]
    assert ind["budget"]["month_total"] == 0.45
    assert ind["budget"]["month_cap"] == settings.budget_monthly_usd
    assert set(ind["queue"]) == {"ready", "flight", "dlq", "active"}
    assert "needs_decision" in ind["counts"]
    assert "tokens" in ind and "cache_hit_pct" in ind["tokens"]
    assert "notifications" in s
    assert s["mock_mode"] is True


def test_action_approve_marks_done(settings, fake_queue):
    from quartermaster.models import Status
    state, jira = make_state(settings, fake_queue)
    res = state.action("approve", "DEMO-101")
    assert res["ok"]
    assert jira.get_ticket("DEMO-101").status == Status.DONE


def test_action_requeue_enqueues(settings, fake_queue):
    state, jira = make_state(settings, fake_queue)
    res = state.action("requeue", "DEMO-102")
    assert res["ok"]
    assert fake_queue.stats()["active"] == 1


def test_action_unknown_ticket(settings, fake_queue):
    state, _ = make_state(settings, fake_queue)
    assert state.action("approve", "NOPE-1")["ok"] is False


def test_timeline_endpoint(settings, fake_queue):
    state, _ = make_state(settings, fake_queue)
    state.obs.record_stage(ticket_key="DEMO-101", stage="plan", verdict="ok")
    tl = state.timeline("DEMO-101")
    assert tl["ticket"] == "DEMO-101"
    assert tl["stages"][0]["stage"] == "plan"
