from quartermaster.budget import BudgetLedger


def test_ticket_total_and_cap(tmp_path):
    led = BudgetLedger(str(tmp_path / "l.db"), per_ticket_usd=3.0, monthly_usd=90.0)
    led.record("DEMO-1", "plan", 0.5)
    led.record("DEMO-1", "implement", 1.0)
    assert led.ticket_total("DEMO-1") == 1.5
    assert not led.ticket_over_cap("DEMO-1")
    led.record("DEMO-1", "implement", 2.0)
    assert led.ticket_over_cap("DEMO-1")


def test_ticket_override(tmp_path):
    led = BudgetLedger(str(tmp_path / "l.db"), per_ticket_usd=3.0, monthly_usd=90.0)
    led.record("DEMO-1", "implement", 2.5)
    assert led.ticket_over_cap("DEMO-1", override=2.0)
    assert not led.ticket_over_cap("DEMO-1", override=5.0)


def test_monthly_killswitch(tmp_path):
    led = BudgetLedger(str(tmp_path / "l.db"), per_ticket_usd=3.0, monthly_usd=5.0)
    assert not led.monthly_exhausted()
    led.record("DEMO-1", "implement", 5.0)
    assert led.monthly_exhausted()
