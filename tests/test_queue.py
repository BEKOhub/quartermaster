from quartermaster.models import Job


def test_priority_ordering(fake_queue):
    fake_queue.enqueue(Job(ticket_key="DEMO-low", priority=8), now=1.0)
    fake_queue.enqueue(Job(ticket_key="DEMO-high", priority=1), now=2.0)
    fake_queue.enqueue(Job(ticket_key="DEMO-mid", priority=5), now=3.0)
    order = [fake_queue.claim(now=10.0).ticket_key for _ in range(3)]
    assert order == ["DEMO-high", "DEMO-mid", "DEMO-low"]


def test_fifo_within_priority(fake_queue):
    fake_queue.enqueue(Job(ticket_key="DEMO-a", priority=5), now=1.0)
    fake_queue.enqueue(Job(ticket_key="DEMO-b", priority=5), now=2.0)
    assert fake_queue.claim(now=10.0).ticket_key == "DEMO-a"
    assert fake_queue.claim(now=10.0).ticket_key == "DEMO-b"


def test_dedupe(fake_queue):
    assert fake_queue.enqueue(Job(ticket_key="DEMO-1"), now=1.0) is True
    assert fake_queue.enqueue(Job(ticket_key="DEMO-1"), now=2.0) is False


def test_ack_frees_ticket(fake_queue):
    fake_queue.enqueue(Job(ticket_key="DEMO-1"), now=1.0)
    job = fake_queue.claim(now=2.0)
    fake_queue.ack(job)
    # ticket freed -> can be enqueued again
    assert fake_queue.enqueue(Job(ticket_key="DEMO-1"), now=3.0) is True


def test_retry_then_dead_letter(fake_queue):
    fake_queue.enqueue(Job(ticket_key="DEMO-x"), now=1.0)
    job = fake_queue.claim(now=2.0)
    assert fake_queue.fail(job, now=3.0, error="boom") == "retry"
    job = fake_queue.claim(now=4.0)
    assert fake_queue.fail(job, now=5.0, error="boom") == "retry"
    job = fake_queue.claim(now=6.0)
    assert fake_queue.fail(job, now=7.0, error="boom") == "dead-lettered"
    assert fake_queue.stats()["dlq"] == 1


def test_reaper_requeues_stuck_job(fake_queue):
    fake_queue.enqueue(Job(ticket_key="DEMO-stuck"), now=1.0)
    fake_queue.claim(now=2.0)  # in flight, deadline = 2 + visibility_timeout
    assert fake_queue.stats()["flight"] == 1
    reaped = fake_queue.reap_expired(now=2.0 + fake_queue.visibility_timeout + 1)
    assert reaped == 1
    assert fake_queue.stats()["ready"] == 1
