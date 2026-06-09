"""Budget ledger (SQLite): records the cost of every Claude call, enforces a
per-ticket cap and a monthly cap, and exposes a kill-switch check the poller
uses to stop picking up new work near the monthly cap.

Zero LLM tokens — pure bookkeeping. `total_cost_usd` from each `claude -p` JSON
result is appended here.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Optional

from .logging_setup import get_logger

log = get_logger("budget")


class BudgetLedger:
    def __init__(self, db_path: str, *, per_ticket_usd: float, monthly_usd: float) -> None:
        self.db_path = db_path
        self.per_ticket_usd = per_ticket_usd
        self.monthly_usd = monthly_usd
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS spend (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_key TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    cost_usd REAL NOT NULL,
                    month TEXT NOT NULL,
                    ts TEXT NOT NULL
                )"""
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_spend_ticket ON spend(ticket_key)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_spend_month ON spend(month)")

    @staticmethod
    def _month() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m")

    # --- recording --------------------------------------------------------
    def record(self, ticket_key: str, stage: str, cost_usd: float) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO spend (ticket_key, stage, cost_usd, month, ts) VALUES (?,?,?,?,?)",
                (ticket_key, stage, cost_usd, self._month(),
                 datetime.now(timezone.utc).isoformat()),
            )
        log.info("spend +$%.4f ticket=%s stage=%s", cost_usd, ticket_key, stage)

    # --- queries ----------------------------------------------------------
    def ticket_total(self, ticket_key: str) -> float:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(cost_usd),0) AS t FROM spend WHERE ticket_key=?",
                (ticket_key,),
            ).fetchone()
        return float(row["t"])

    def month_total(self) -> float:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(cost_usd),0) AS t FROM spend WHERE month=?",
                (self._month(),),
            ).fetchone()
        return float(row["t"])

    # --- caps -------------------------------------------------------------
    def ticket_cap(self, override: Optional[float]) -> float:
        return override if override is not None else self.per_ticket_usd

    def ticket_over_cap(self, ticket_key: str, override: Optional[float] = None) -> bool:
        return self.ticket_total(ticket_key) >= self.ticket_cap(override)

    def monthly_exhausted(self) -> bool:
        """Kill-switch: True once the monthly cap is reached. The poller stops
        enqueuing new work while this is True."""
        return self.month_total() >= self.monthly_usd
