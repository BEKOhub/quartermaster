"""A dependency-free web dashboard (stdlib http.server) served by the agent,
sharing the live broker / queue / budget / observability store.

  GET  /                  -> the HTML board page
  GET  /api/state         -> JSON: board + indicators + audit + notifications
  GET  /api/ticket/<KEY>  -> JSON: per-ticket run timeline (drill-down)
  GET  /api/stream        -> Server-Sent Events: pushes state ~every 2s
  POST /api/action        -> {action, key}: requeue | answer_adr | approve

Authentication: if DASHBOARD_TOKEN is set, every request must supply it via
  - query param:  ?token=<value>
  - header:       Authorization: Bearer <value>

Runs in background threads; stops when the shutdown event is set.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .budget import BudgetLedger
from .config import Settings
from .logging_setup import get_logger
from .models import Job, Status

log = get_logger("dashboard")

_HTML_PATH = os.path.join(os.path.dirname(__file__), "dashboard.html")

_COLUMNS = [
    (Status.TODO, "To Do", "gray"),
    (Status.QUEUED, "Queued", "blue"),
    (Status.IN_PROGRESS, "In Progress", "indigo"),
    (Status.NEEDS_DECISION, "Needs Decision", "amber"),
    (Status.IN_REVIEW, "In Review", "purple"),
    (Status.BLOCKED, "Blocked", "red"),
    (Status.DONE, "Done", "green"),
]


class DashboardState:
    def __init__(self, settings: Settings, broker, queue, budget: BudgetLedger,
                 obs, notifier) -> None:
        self.s = settings
        self.broker = broker
        self.queue = queue
        self.budget = budget
        self.obs = obs
        self.notifier = notifier

    def build(self) -> dict[str, Any]:
        tickets = self.broker.board()
        by_status: dict[Status, list] = {st: [] for st, _, _ in _COLUMNS}
        for t in tickets:
            by_status.setdefault(t.status, []).append(t)

        columns, counts = [], {}
        for st, label, color in _COLUMNS:
            items = by_status.get(st, [])
            counts[st.value] = len(items)
            columns.append({
                "key": st.value, "label": label, "color": color,
                "tickets": [{
                    "key": t.key, "summary": t.summary, "status": st.value,
                    "priority": t.priority, "labels": t.labels,
                    "assignee": t.assignee, "is_hard": t.is_hard,
                    "cost": round(self.budget.ticket_total(t.key), 2),
                } for t in items],
            })

        month_total = self.budget.month_total()
        cap = self.s.budget_monthly_usd or 1.0
        summ = self.obs.summary()
        return {
            "mock_mode": self.s.mock_mode,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "columns": columns,
            "indicators": {
                "queue": self.queue.stats(),
                "budget": {
                    "month_total": round(month_total, 2),
                    "month_cap": self.s.budget_monthly_usd,
                    "per_ticket_cap": self.s.budget_per_ticket_usd,
                    "pct": round(100.0 * month_total / cap, 1),
                },
                "tokens": {
                    "input": summ["input_tokens"], "output": summ["output_tokens"],
                    "cache_read": summ["cache_read_tokens"],
                    "cache_hit_pct": round(summ["cache_hit_ratio"] * 100, 1),
                },
                "counts": counts,
            },
            "audit": self._recent_audit(),
            "notifications": list(reversed(list(self.notifier.sent)[-12:])),
        }

    def timeline(self, key: str) -> dict[str, Any]:
        return {"ticket": key, "stages": self.obs.timeline(key)}

    def _recent_audit(self, n: int = 40) -> list[dict]:
        path = self.s.audit_log_path
        if not os.path.exists(path):
            return []
        try:
            with open(path, encoding="utf-8") as fh:
                lines = fh.readlines()[-n:]
        except OSError:
            return []
        out = []
        for line in reversed(lines):
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out

    # --- actions (human-in-the-loop) -------------------------------------
    def action(self, action: str, key: str) -> dict[str, Any]:
        t = self.broker.get_ticket(key)
        if t is None:
            return {"ok": False, "error": f"unknown ticket {key}"}
        if action == "approve":
            self.broker.comment(key, "👍 Approved & merged by boss (from dashboard).")
            self.broker.set_status(key, Status.DONE)
            return {"ok": True, "message": f"{key} marked Done"}
        if action in ("requeue", "answer_adr"):
            if action == "answer_adr":
                # Simulate the boss answering: record a decision and let the
                # agent proceed (mock removes the escalating label so it won't
                # re-escalate; in real Jira you'd just reply + reassign).
                self.broker.comment(key, "🗳️ Decision recorded by boss: proceed with the "
                                          "recommended option (from dashboard).")
                if self.s.mock_mode:
                    t.labels = [l for l in t.labels
                                if l.lower() not in ("arch", "hard", "structural")]
            self.broker.assign_to_agent(key)
            self.broker.set_status(key, Status.TODO)
            self.queue.enqueue(Job(ticket_key=key, priority=2), now=time.time())
            self.broker.set_status(key, Status.QUEUED)
            return {"ok": True, "message": f"{key} re-queued for the agent"}
        return {"ok": False, "error": f"unknown action {action}"}


def _check_token(token: str, request_token: str) -> bool:
    """Constant-time token comparison to prevent timing attacks."""
    return hmac.compare_digest(
        hashlib.sha256(token.encode()).digest(),
        hashlib.sha256(request_token.encode()).digest(),
    )


def _make_handler(state: DashboardState, html: str, token: str = ""):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body: bytes, ctype: str, extra=None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _authed(self) -> bool:
            """Return True if the request is authenticated (or no token is required)."""
            if not token:
                return True
            # Check Authorization: Bearer <token> header.
            auth = self.headers.get("Authorization", "")
            if auth.startswith("Bearer ") and _check_token(token, auth[7:].strip()):
                return True
            # Check ?token= query param.
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            req_token = (params.get("token") or [""])[0]
            if req_token and _check_token(token, req_token):
                return True
            return False

        def do_GET(self):  # noqa: N802
            # Health check is always unauthenticated.
            if self.path in ("/healthz", "/health"):
                self._send(200, b'{"status":"ok"}', "application/json")
                return
            if not self._authed():
                self._send(401, b'{"error":"unauthorized"}', "application/json")
                return
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                self._send(200, html.encode(), "text/html; charset=utf-8")
            elif path.startswith("/api/state"):
                self._json(state.build())
            elif path.startswith("/api/ticket/"):
                key = path.rsplit("/", 1)[-1]
                self._json(state.timeline(key))
            elif path.startswith("/api/stream"):
                self._stream()
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):  # noqa: N802
            if not self._authed():
                self._send(401, b'{"error":"unauthorized"}', "application/json")
                return
            if not urlparse(self.path).path.startswith("/api/action"):
                self._send(404, b"not found", "text/plain")
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length) or b"{}")
                result = state.action(payload.get("action", ""), payload.get("key", ""))
                self._json(result)
            except Exception as e:  # noqa: BLE001
                log.exception("action failed")
                self._json({"ok": False, "error": str(e)}, code=500)

        def _json(self, obj, code=200):
            try:
                self._send(code, json.dumps(obj).encode(), "application/json")
            except Exception:
                log.exception("json send failed")

        def _stream(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            # No hard cap — reconnect is client's responsibility.
            # Send a keep-alive comment every 30s so proxies don't time out.
            last_keepalive = time.monotonic()
            try:
                while True:
                    data = json.dumps(state.build())
                    self.wfile.write(f"data: {data}\n\n".encode())
                    self.wfile.flush()
                    time.sleep(2)
                    if time.monotonic() - last_keepalive > 30:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        last_keepalive = time.monotonic()
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):  # silence
            pass

    return Handler


def serve_dashboard(settings: Settings, broker, queue, budget: BudgetLedger,
                    obs, notifier, stop_event: threading.Event) -> threading.Thread:
    try:
        with open(_HTML_PATH, encoding="utf-8") as fh:
            html = fh.read()
    except OSError:
        html = "<h1>dashboard.html missing</h1>"
    state = DashboardState(settings, broker, queue, budget, obs, notifier)
    handler = _make_handler(state, html, token=settings.dashboard_token)
    httpd = ThreadingHTTPServer(("0.0.0.0", settings.dashboard_port), handler)

    def _run():
        log.info("dashboard at http://localhost:%s", settings.dashboard_port)
        httpd.serve_forever(poll_interval=0.5)

    def _watch_stop():
        stop_event.wait()
        httpd.shutdown()

    t = threading.Thread(target=_run, name="dashboard", daemon=True)
    t.start()
    threading.Thread(target=_watch_stop, name="dashboard-stop", daemon=True).start()
    return t
