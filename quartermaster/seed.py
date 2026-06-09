"""One-shot helper (`python -m quartermaster.seed` / `make seed`): run a single intake
poll cycle and print queue stats. Handy for demos and for confirming the
poller can reach Jira in non-mock mode without waiting for the interval.
"""
from __future__ import annotations

from .audit import AuditLog
from .broker.factory import build_broker
from .budget import BudgetLedger
from .config import get_settings
from .logging_setup import get_logger, setup_logging
from .queue import JobQueue
from .poller import Poller


def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    log = get_logger("seed")
    audit = AuditLog(settings.audit_log_path)
    broker = build_broker(settings, audit)
    queue = JobQueue(settings.redis_url, settings.queue_namespace,
                     max_attempts=settings.job_max_attempts,
                     visibility_timeout=settings.job_visibility_timeout)
    budget = BudgetLedger(settings.budget_db_path,
                          per_ticket_usd=settings.budget_per_ticket_usd,
                          monthly_usd=settings.budget_monthly_usd)
    poller = Poller(settings, broker, queue, budget)
    n = poller.poll_once()
    log.info("seed: enqueued %s ticket(s); queue=%s", n, queue.stats())


if __name__ == "__main__":
    main()
