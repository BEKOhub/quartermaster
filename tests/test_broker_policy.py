import pytest

from quartermaster.audit import AuditLog
from quartermaster.broker.broker import SecretsBroker, PolicyError
from quartermaster.broker.connectors.mock import MockGitHubConnector, MockJiraConnector
from quartermaster.models import Status


def make_broker(settings):
    audit = AuditLog(settings.audit_log_path)
    return SecretsBroker(settings, audit, MockJiraConnector(), MockGitHubConnector())


def test_allowed_jira_ops(settings):
    broker = make_broker(settings)
    tickets = broker.search_todo()
    assert tickets  # seeded mock tickets
    broker.comment("DEMO-101", "hi")
    broker.set_status("DEMO-101", Status.IN_PROGRESS)


def test_denied_op_raises_and_audits(settings):
    broker = make_broker(settings)
    with pytest.raises(PolicyError):
        broker._gate("stripe", "write_live")
    with pytest.raises(PolicyError):
        broker._gate("github", "merge_main")
    # the denial was recorded in the audit log
    with open(settings.audit_log_path, encoding="utf-8") as fh:
        content = fh.read()
    assert "DENIED" in content


def test_open_pr_allowed(settings):
    broker = make_broker(settings)
    url = broker.open_pr(ticket_key="DEMO-102", branch="agent/DEMO-102-x",
                         title="t", body="b")
    assert url.startswith("https://github.com/")
