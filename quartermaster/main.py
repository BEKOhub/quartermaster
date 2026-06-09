"""Entrypoint: assemble the control plane and run the selected role(s) until a
signal arrives. Everything here is deterministic glue — zero LLM tokens.

ROLE controls what this process runs (lets you scale workers horizontally):
  all       — poller + workers + dashboard (default; single-box)
  poller    — intake only (run exactly ONE of these)
  worker    — pipeline workers only (scale to N replicas)
  dashboard — the web UI only
"""
from __future__ import annotations

import signal
import threading
from dataclasses import dataclass

from .audit import AuditLog
from .broker import SecretsBroker
from .broker.factory import build_broker
from .budget import BudgetLedger
from .claude_mock import mock_brain
from .claude_runner import ClaudeRunner
from .config import get_settings
from .logging_setup import get_logger, setup_logging
from .notify import Notifier
from .observability import Observability
from .pipeline import Pipeline
from .poller import Poller
from .queue import JobQueue
from .repomap import RepoMap
from .worker import WorkerPool
from .worktree import WorktreeManager


@dataclass
class Stack:
    settings: object
    broker: SecretsBroker
    queue: JobQueue
    budget: BudgetLedger
    obs: Observability
    notifier: Notifier
    pipeline: Pipeline
    poller: Poller
    workers: WorkerPool


def build_stack(settings) -> Stack:
    audit = AuditLog(settings.audit_log_path)
    broker = build_broker(settings, audit)
    queue = JobQueue(settings.redis_url, settings.queue_namespace,
                     max_attempts=settings.job_max_attempts,
                     visibility_timeout=settings.job_visibility_timeout)
    budget = BudgetLedger(settings.budget_db_path,
                          per_ticket_usd=settings.budget_per_ticket_usd,
                          monthly_usd=settings.budget_monthly_usd)
    obs = Observability(settings.runs_db_path, otel_enabled=settings.otel_enabled)
    notifier = Notifier(settings.notify_webhook_url, mock_mode=settings.mock_mode)
    runner = ClaudeRunner(settings, mock_provider=mock_brain if settings.mock_mode else None)
    worktrees = WorktreeManager(settings)
    repomap = RepoMap(settings.repo_path, max_files=settings.repo_map_max_files)
    pipeline = Pipeline(settings, broker, budget, runner, worktrees, obs,
                        repomap=repomap, notifier=notifier)
    poller = Poller(settings, broker, queue, budget)
    workers = WorkerPool(settings, queue, pipeline, broker)
    return Stack(settings, broker, queue, budget, obs, notifier, pipeline, poller, workers)


def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    log = get_logger("main")
    role = (settings.role or "all").lower()
    log.info("Quartermaster starting (role=%s mock_mode=%s)", role, settings.mock_mode)

    s = build_stack(settings)
    log.info("queue at startup: %s", s.queue.stats())

    stop_event = threading.Event()

    def _handle(signum, _frame):
        log.info("signal %s received — shutting down", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)

    threads: list[threading.Thread] = []

    if role in ("all", "dashboard") and settings.dashboard_enabled:
        from .dashboard import serve_dashboard
        serve_dashboard(settings, s.broker, s.queue, s.budget, s.obs, s.notifier, stop_event)

    if role in ("all", "worker"):
        s.workers.start(stop_event)

    if role in ("all", "poller"):
        t = threading.Thread(target=s.poller.run_forever, args=(stop_event,),
                             name="poller", daemon=True)
        t.start()
        threads.append(t)

    if role == "dashboard":
        # nothing else to run; just block until signalled
        log.info("dashboard-only mode")

    stop_event.wait()
    for t in threads:
        t.join(timeout=5)
    if role in ("all", "worker"):
        s.workers.join()
    log.info("shutdown complete")


if __name__ == "__main__":
    main()
