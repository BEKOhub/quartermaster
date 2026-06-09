from quartermaster.models import ClaudeResult
from quartermaster.observability import Observability


def test_record_and_timeline(tmp_path):
    obs = Observability(str(tmp_path / "runs.db"))
    r = ClaudeResult(session_id="s", cost_usd=0.35, input_tokens=300,
                     output_tokens=100, cache_read_tokens=1500, duration_ms=42)
    obs.record_stage(ticket_key="DEMO-1", stage="implement", model="sonnet",
                     verdict="done", result=r, attempt=1)
    tl = obs.timeline("DEMO-1")
    assert len(tl) == 1
    assert tl[0]["stage"] == "implement"
    assert tl[0]["cost_usd"] == 0.35
    assert tl[0]["cache_read_tokens"] == 1500


def test_summary_cache_hit_ratio(tmp_path):
    obs = Observability(str(tmp_path / "runs.db"))
    r = ClaudeResult(session_id="s", cost_usd=0.1, input_tokens=100,
                     output_tokens=50, cache_read_tokens=900)
    obs.record_stage(ticket_key="DEMO-1", stage="plan", result=r)
    summ = obs.summary()
    # cache_read / (input + cache_read) = 900 / 1000 = 0.9
    assert summ["cache_hit_ratio"] == 0.9
    assert summ["stages"] == 1


def test_otel_unavailable_degrades(tmp_path):
    # Requesting OTel without the packages installed must not raise.
    obs = Observability(str(tmp_path / "runs.db"), otel_enabled=True)
    obs.record_stage(ticket_key="DEMO-1", stage="plan", verdict="ok")
    assert obs.summary()["stages"] == 1
