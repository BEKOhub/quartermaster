"""Observability: durable run history (SQLite) + optional OpenTelemetry spans.

Every pipeline stage is recorded here with the GenAI-style attributes the 2026
OTel conventions expect (model, token usage, cost, duration, verdict). The SQLite
table powers the dashboard timeline and the eval scorecard; OTel export (off by
default, no hard dependency) ships the same spans to Phoenix/Tempo/Jaeger.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

from .logging_setup import get_logger
from .models import ClaudeResult

log = get_logger("observ")


class Observability:
    def __init__(self, db_path: str, *, otel_enabled: bool = False,
                 service_name: str = "quartermaster") -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._init_db()
        self._tracer = self._init_otel(service_name) if otel_enabled else None

    # --- OTel (optional, lazy) -------------------------------------------
    def _init_otel(self, service_name: str):
        try:
            from opentelemetry import trace
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import (BatchSpanProcessor)
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter)
            provider = TracerProvider(resource=Resource.create(
                {"service.name": service_name}))
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
            trace.set_tracer_provider(provider)
            log.info("OpenTelemetry tracing enabled (OTLP/HTTP exporter)")
            return trace.get_tracer(service_name)
        except Exception as e:  # missing pkg / no collector — degrade gracefully
            log.warning("OTel requested but unavailable (%s); spans go to SQLite only", e)
            return None

    # --- schema -----------------------------------------------------------
    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_key TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    model TEXT,
                    verdict TEXT,
                    cost_usd REAL DEFAULT 0,
                    input_tokens INTEGER DEFAULT 0,
                    output_tokens INTEGER DEFAULT 0,
                    cache_read_tokens INTEGER DEFAULT 0,
                    cache_write_tokens INTEGER DEFAULT 0,
                    duration_ms INTEGER DEFAULT 0,
                    attempt INTEGER DEFAULT 0,
                    detail TEXT,
                    ts TEXT NOT NULL
                )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_ticket ON runs(ticket_key)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_ts ON runs(ts)")
            # Idempotent: add cache_write_tokens to existing databases.
            try:
                conn.execute("ALTER TABLE runs ADD COLUMN cache_write_tokens INTEGER DEFAULT 0")
            except Exception:
                pass  # column already exists

    # --- recording --------------------------------------------------------
    def record_stage(self, *, ticket_key: str, stage: str, model: str = "",
                     verdict: str = "", result: Optional[ClaudeResult] = None,
                     duration_ms: int = 0, attempt: int = 0, detail: str = "") -> None:
        r = result
        cost = r.cost_usd if r else 0.0
        intok = r.input_tokens if r else 0
        outtok = r.output_tokens if r else 0
        cache_read = r.cache_read_tokens if r else 0
        cache_write = r.cache_write_tokens if r else 0
        dur = duration_ms or (r.duration_ms if r else 0)
        with self._lock, self._conn() as conn:
            conn.execute(
                """INSERT INTO runs (ticket_key, stage, model, verdict, cost_usd,
                       input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
                       duration_ms, attempt, detail, ts)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (ticket_key, stage, model, verdict, cost, intok, outtok,
                 cache_read, cache_write, dur, attempt, detail,
                 datetime.now(timezone.utc).isoformat()))
        if self._tracer is not None:
            self._emit_span(ticket_key, stage, model, verdict, cost, intok, outtok,
                            cache_read, dur, attempt)

    def prune(self, keep_days: int = 90) -> int:
        """Delete run records older than keep_days. Returns rows deleted."""
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).isoformat()
        with self._lock, self._conn() as conn:
            cur = conn.execute("DELETE FROM runs WHERE ts < ?", (cutoff,))
            deleted = cur.rowcount
        if deleted:
            log.info("pruned %s run records older than %s days", deleted, keep_days)
        return deleted

    def _emit_span(self, ticket, stage, model, verdict, cost, intok, outtok,
                   cache, dur, attempt) -> None:
        try:
            start = time.time_ns() - dur * 1_000_000
            span = self._tracer.start_span(f"pipeline.{stage}", start_time=start)
            # GenAI semantic conventions (gen_ai.*) + agent attributes.
            span.set_attribute("gen_ai.system", "anthropic")
            span.set_attribute("gen_ai.request.model", model or "unknown")
            span.set_attribute("gen_ai.usage.input_tokens", intok)
            span.set_attribute("gen_ai.usage.output_tokens", outtok)
            span.set_attribute("gen_ai.usage.cache_read_tokens", cache)
            span.set_attribute("agent.ticket", ticket)
            span.set_attribute("agent.stage", stage)
            span.set_attribute("agent.verdict", verdict)
            span.set_attribute("agent.attempt", attempt)
            span.set_attribute("agent.cost_usd", cost)
            span.end()
        except Exception:
            log.debug("otel span emit failed", exc_info=True)

    # --- queries (dashboard + evals) -------------------------------------
    def timeline(self, ticket_key: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM runs WHERE ticket_key=? ORDER BY id ASC",
                (ticket_key,)).fetchall()
        return [dict(r) for r in rows]

    def recent(self, limit: int = 60) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def summary(self) -> dict[str, Any]:
        with self._conn() as conn:
            row = conn.execute(
                """SELECT COUNT(*) AS stages,
                          COALESCE(SUM(cost_usd),0) AS cost,
                          COALESCE(SUM(input_tokens),0) AS intok,
                          COALESCE(SUM(output_tokens),0) AS outtok,
                          COALESCE(SUM(cache_read_tokens),0) AS cache_read,
                          COALESCE(SUM(cache_write_tokens),0) AS cache_write
                   FROM runs""").fetchone()
            billed = (row["intok"] or 0) + (row["cache_read"] or 0)
            return {
                "stages": row["stages"],
                "cost_usd": round(row["cost"], 4),
                "input_tokens": row["intok"],
                "output_tokens": row["outtok"],
                "cache_read_tokens": row["cache_read"],
                "cache_write_tokens": row["cache_write"],
                "cache_hit_ratio": round((row["cache_read"] / billed) if billed else 0.0, 3),
            }
