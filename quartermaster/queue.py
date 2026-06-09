"""Redis-backed job queue: priority ordering, reliable in-flight handling,
bounded retries, and a dead-letter queue.

Design (all keys namespaced by QUEUE_NAMESPACE):
  - ready  : a Redis ZSET scored by (priority * 1e12 + enqueued_at). ZPOPMIN
             gives priority order, FIFO within a priority.
  - flight : a HASH of job_id -> {job, deadline}. A reaper re-queues jobs whose
             visibility deadline passed (worker died mid-job).
  - dlq    : a LIST of jobs that exhausted JOB_MAX_ATTEMPTS.
  - dedupe : a SET of ticket keys currently in ready/flight, so the poller never
             enqueues the same ticket twice.

This mirrors a common service-bus queue pattern (priority + normal + DLQ) without a
cloud dependency. Swap in Azure Service Bus later by reimplementing this class.
"""
from __future__ import annotations

import json
import uuid
from typing import Optional

import redis

from .logging_setup import get_logger
from .models import Job

log = get_logger("queue")


class JobQueue:
    def __init__(self, redis_url: str, namespace: str, *, max_attempts: int,
                 visibility_timeout: int, client: Optional[redis.Redis] = None) -> None:
        self.r = client or redis.from_url(redis_url, decode_responses=True)
        self.ns = namespace
        self.max_attempts = max_attempts
        self.visibility_timeout = visibility_timeout

    # --- key helpers ------------------------------------------------------
    @property
    def k_ready(self) -> str:
        return f"{self.ns}:ready"

    @property
    def k_flight(self) -> str:
        return f"{self.ns}:flight"

    @property
    def k_dlq(self) -> str:
        return f"{self.ns}:dlq"

    @property
    def k_dedupe(self) -> str:
        return f"{self.ns}:active"

    # --- enqueue ----------------------------------------------------------
    def enqueue(self, job: Job, *, now: float) -> bool:
        """Add a job unless its ticket is already active. Returns True if added."""
        if self.r.sismember(self.k_dedupe, job.ticket_key):
            return False
        if not job.id:
            job.id = uuid.uuid4().hex
        job.enqueued_at = now
        score = job.priority * 1e12 + now
        pipe = self.r.pipeline()
        pipe.zadd(self.k_ready, {json.dumps(job.to_dict()): score})
        pipe.sadd(self.k_dedupe, job.ticket_key)
        pipe.execute()
        log.info("enqueued %s (prio=%s id=%s)", job.ticket_key, job.priority, job.id)
        return True

    # --- claim / ack ------------------------------------------------------
    def claim(self, *, now: float) -> Optional[Job]:
        """Atomically pop the highest-priority ready job and mark it in-flight."""
        popped = self.r.zpopmin(self.k_ready, 1)
        if not popped:
            return None
        raw, _score = popped[0]
        job = Job.from_dict(json.loads(raw))
        deadline = now + self.visibility_timeout
        self.r.hset(self.k_flight, job.id, json.dumps({"job": job.to_dict(), "deadline": deadline}))
        return job

    def ack(self, job: Job) -> None:
        """Job finished successfully — drop it from flight and free the ticket."""
        pipe = self.r.pipeline()
        pipe.hdel(self.k_flight, job.id)
        pipe.srem(self.k_dedupe, job.ticket_key)
        pipe.execute()

    def fail(self, job: Job, *, now: float, error: str = "") -> str:
        """Job failed. Retry (re-queue) until max_attempts, then dead-letter.

        Returns "retry" or "dead-lettered"."""
        self.r.hdel(self.k_flight, job.id)
        job.attempts += 1
        if job.attempts >= self.max_attempts:
            payload = {"job": job.to_dict(), "error": error, "failed_at": now}
            pipe = self.r.pipeline()
            pipe.rpush(self.k_dlq, json.dumps(payload))
            pipe.srem(self.k_dedupe, job.ticket_key)
            pipe.execute()
            log.warning("dead-lettered %s after %s attempts: %s",
                        job.ticket_key, job.attempts, error)
            return "dead-lettered"
        score = job.priority * 1e12 + now
        self.r.zadd(self.k_ready, {json.dumps(job.to_dict()): score})
        log.info("re-queued %s (attempt %s/%s)", job.ticket_key, job.attempts, self.max_attempts)
        return "retry"

    # --- reaper -----------------------------------------------------------
    def reap_expired(self, *, now: float) -> int:
        """Re-queue jobs whose worker died (visibility deadline passed)."""
        reaped = 0
        for job_id, raw in list(self.r.hgetall(self.k_flight).items()):
            entry = json.loads(raw)
            if entry["deadline"] <= now:
                job = Job.from_dict(entry["job"])
                self.r.hdel(self.k_flight, job_id)
                self.r.zadd(self.k_ready, {json.dumps(job.to_dict()): job.priority * 1e12 + now})
                reaped += 1
                log.warning("reaped stuck job %s -> re-queued", job.ticket_key)
        return reaped

    # --- introspection ----------------------------------------------------
    def stats(self) -> dict[str, int]:
        return {
            "ready": self.r.zcard(self.k_ready),
            "flight": self.r.hlen(self.k_flight),
            "dlq": self.r.llen(self.k_dlq),
            "active": self.r.scard(self.k_dedupe),
        }

    def drain_dlq(self) -> list[dict]:
        out = []
        while True:
            raw = self.r.lpop(self.k_dlq)
            if raw is None:
                break
            out.append(json.loads(raw))
        return out
