from quartermaster.models import Ticket
from quartermaster.session import session_id_for


def test_hard_label_detection():
    assert Ticket(key="DEMO-1", summary="x", labels=["arch"]).is_hard
    assert Ticket(key="DEMO-1", summary="x", labels=["HARD"]).is_hard
    assert not Ticket(key="DEMO-1", summary="x", labels=["bug"]).is_hard


def test_budget_override_parsing():
    assert Ticket(key="DEMO-1", summary="x", labels=["budget:2"]).budget_override == 2.0
    assert Ticket(key="DEMO-1", summary="x", labels=["budget: 5"]).budget_override == 5.0
    assert Ticket(key="DEMO-1", summary="x", labels=[]).budget_override is None


def test_slug():
    assert Ticket(key="DEMO-1", summary="Fix the Login Bug!").slug == "fix-the-login-bug"


def test_session_id_deterministic():
    a = session_id_for("DEMO-123")
    b = session_id_for("DEMO-123")
    c = session_id_for("DEMO-123", variant="review")
    assert a == b
    assert a != c
