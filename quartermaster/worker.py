"""Worker pool: a concurrency-capped set of threads that claim jobs from the
queue and run the pipeline. This is the throttle that keeps a mini PC (and the
token bill) sane. A job that raises is failed back to the queue (retry → DLQ);
a dead-lettered job marks its ticket Blocked.
"""
from __future__ import annotations

import random
import threading
import time

from .broker import SecretsBroker
from .config import Settings
from .logging_setup import get_logger
from .models import Status
from .pipeline import Pipeline
from .queue import JobQueue

log = get_logger("worker")

_IDLE_BACKOFF_BASE = 1.0
_IDLE_BACKOFF_JITTER = 0.5  # ±jitter so multiple workers don't thunderherd-wake


class WorkerPool:
    def __init__(self, settings: Settings, queue: JobQueue, pipeline: Pipeline,
                 broker: SecretsBroker, *, clock=time.time) -> None:
        self.s = settings
        self.queue = queue
        self.pipeline = pipeline
        self.broker = broker
        self._clock = clock
        self._threads: list[threading.Thread] = []

    def start(self, stop_event: threading.Event) -> None:
        n = max(1, self.s.worker_concurrency)
        log.info("starting %s worker(s)", n)
        for i in range(n):
            t = threading.Thread(target=self._loop, args=(stop_event, i), daemon=True,
                                 name=f"worker-{i}")
            t.start()
            self._threads.append(t)

    def join(self) -> None:
        for t in self._threads:
            t.join(timeout=10)

    def _loop(self, stop_event: threading.Event, idx: int) -> None:
        log.info("worker-%s online", idx)
        while not stop_event.is_set():
            job = self.queue.claim(now=self._clock())
            if job is None:
                jitter = random.uniform(-_IDLE_BACKOFF_JITTER, _IDLE_BACKOFF_JITTER)
                stop_event.wait(_IDLE_BACKOFF_BASE + jitter)
                continue
            self._process(job)
        log.info("worker-%s offline", idx)

    def _process(self, job) -> None:
        log.info("worker picked up %s", job.ticket_key)
        try:
            ticket = self.broker.get_ticket(job.ticket_key)
            if ticket is None:
                self.queue.ack(job)
                log.warning("ticket %s vanished — acked", job.ticket_key)
                return
            result = self.pipeline.run(ticket)
            log.info("%s -> %s (cost $%.2f)", job.ticket_key, result.outcome.value,
                     result.cost_usd)
            self.queue.ack(job)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:  # pipeline blew up — let the queue retry / DLQ
            log.exception("pipeline error on %s", job.ticket_key)
            disposition = self.queue.fail(job, now=self._clock(), error=str(e))
            if disposition == "dead-lettered":
                self._dead_letter(job, str(e))

    def _dead_letter(self, job, error: str) -> None:
        steps = [
            ("comment", lambda: self.broker.comment(
                job.ticket_key,
                f"⛔ Failed after {self.s.job_max_attempts} attempts: {error[:300]}")),
            ("set_status_blocked", lambda: self.broker.set_status(
                job.ticket_key, Status.BLOCKED)),
            ("assign_to_boss", lambda: self.broker.assign_to_boss(job.ticket_key)),
        ]
        for name, fn in steps:
            try:
                fn()
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                log.exception("dead-letter step %s failed for %s — continuing", name,
                              job.ticket_key)
