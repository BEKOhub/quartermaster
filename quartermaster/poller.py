"""Intake poller: every POLL_INTERVAL_SECONDS, ask the broker for To-Do tickets
assigned to the agent, enqueue one job per ticket, and move the ticket to Queued
so it isn't picked up twice. Honours the monthly budget kill-switch.
"""
from __future__ import annotations

import time

from .broker import SecretsBroker
from .budget import BudgetLedger
from .config import Settings
from .logging_setup import get_logger
from .models import Job, Status
from .queue import JobQueue

log = get_logger("poller")

# Jira priority name -> queue priority (lower = more urgent).
_PRIORITY = {"highest": 1, "high": 2, "medium": 5, "low": 8, "lowest": 9}


class Poller:
    def __init__(self, settings: Settings, broker: SecretsBroker,
                 queue: JobQueue, budget: BudgetLedger, *, clock=time.time) -> None:
        self.s = settings
        self.broker = broker
        self.queue = queue
        self.budget = budget
        self._clock = clock

    def poll_once(self) -> int:
        """One intake cycle. Returns the number of jobs enqueued."""
        if self.budget.monthly_exhausted():
            log.warning("monthly budget exhausted ($%.2f/$%.2f) — not enqueuing new work",
                        self.budget.month_total(), self.s.budget_monthly_usd)
            return 0
        tickets = self.broker.search_todo()
        enqueued = 0
        for t in tickets:
            job = Job(ticket_key=t.key,
                      priority=_PRIORITY.get(t.priority.lower(), 5))
            if self.queue.enqueue(job, now=self._clock()):
                self.broker.set_status(t.key, Status.QUEUED)
                self.broker.comment(t.key, "📥 Queued for the agent.")
                enqueued += 1
        if enqueued:
            log.info("enqueued %s ticket(s); queue=%s", enqueued, self.queue.stats())
        return enqueued

    def run_forever(self, stop_event) -> None:
        log.info("poller started (interval=%ss)", self.s.poll_interval_seconds)
        while not stop_event.is_set():
            try:
                self.queue.reap_expired(now=self._clock())
                self.poll_once()
            except Exception:  # never let one bad cycle kill the poller
                log.exception("poll cycle failed")
            stop_event.wait(self.s.poll_interval_seconds)
        log.info("poller stopped")
