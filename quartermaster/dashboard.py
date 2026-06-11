"""A dependency-free web dashboard (stdlib http.server) served by the agent,
sharing the live broker / queue / budget / observability store.

  GET  /                      -> the HTML board page
  GET  /setup                 -> connection setup wizard
  GET  /api/state             -> JSON: board + indicators + audit + notifications
  GET  /api/ticket/<KEY>      -> JSON: per-ticket run timeline (drill-down)
  GET  /api/stream            -> Server-Sent Events: pushes state ~every 2s
  POST /api/action            -> {action, key}: requeue | answer_adr | approve
  GET  /api/setup/load        -> current .env values (secrets masked)
  POST /api/setup/validate/github -> validate GitHub PAT, return user + repos
  POST /api/setup/validate/jira   -> validate Jira creds, return projects
  POST /api/setup/jira-statuses   -> auto-detect workflow statuses for a project
  POST /api/setup/save            -> write .env

Authentication: if DASHBOARD_TOKEN is set, every request must supply it via
  - query param:  ?token=<value>
  - header:       Authorization: Bearer <value>
  Setup routes (/setup, /api/setup/*) bypass auth when DASHBOARD_TOKEN is not
  yet configured — allowing first-time setup without a chicken-and-egg problem.

Runs in background threads; stops when the shutdown event is set.
"""
from __future__ import annotations

import base64
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

import requests as _req

from .budget import BudgetLedger
from .config import Settings
from .logging_setup import get_logger
from .models import Job, Status

log = get_logger("dashboard")

_SETUP_HTML_PATH = os.path.join(os.path.dirname(__file__), "setup.html")


# ---------------------------------------------------------------------------
# Setup API helpers — pure functions, no dependency on dashboard state
# ---------------------------------------------------------------------------

def _setup_load_env() -> dict:
    env_path = os.environ.get("ENV_FILE_PATH", ".env")
    values: dict[str, str] = {}
    if os.path.exists(env_path):
        try:
            with open(env_path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, _, v = line.partition("=")
                        values[k.strip()] = v.strip()
        except OSError:
            pass
    _SECRETS = {"ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "JIRA_API_TOKEN",
                "GH_TOKEN", "DASHBOARD_TOKEN", "NOTIFY_WEBHOOK_URL",
                "STRIPE_API_KEY", "AZURE_CLIENT_SECRET", "CLOUDFLARE_API_TOKEN"}
    masked = {k: ("***" if k in _SECRETS and v else v) for k, v in values.items()}
    return {"ok": True, "values": masked, "path": env_path, "exists": os.path.exists(env_path)}


def _setup_save_env(updates: dict) -> dict:
    env_path = os.environ.get("ENV_FILE_PATH", ".env")
    lines: list[str] = []
    if os.path.exists(env_path):
        try:
            with open(env_path, encoding="utf-8") as fh:
                lines = fh.readlines()
        except OSError:
            pass

    applied: set[str] = set()
    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k = stripped.partition("=")[0].strip()
            if k in updates:
                new_lines.append(f"{k}={updates[k]}\n")
                applied.add(k)
                continue
        new_lines.append(line)

    leftover = {k: v for k, v in updates.items() if k not in applied}
    if leftover:
        new_lines.append("\n# Quartermaster setup wizard\n")
        for k, v in leftover.items():
            new_lines.append(f"{k}={v}\n")

    if not lines:
        new_lines = [f"{k}={v}\n" for k, v in updates.items()]

    content = "".join(new_lines)
    try:
        os.makedirs(os.path.dirname(os.path.abspath(env_path)) or ".", exist_ok=True)
        with open(env_path, "w", encoding="utf-8") as fh:
            fh.write(content)
        return {"ok": True, "path": env_path}
    except OSError as e:
        return {"ok": False, "error": str(e), "content": content}


def _setup_validate_github(token: str) -> dict:
    try:
        s = _req.Session()
        s.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        ur = s.get("https://api.github.com/user", timeout=10)
        if ur.status_code == 401:
            return {"ok": False, "error": "Token rejected (401) — check the PAT value and scopes"}
        if not ur.ok:
            return {"ok": False, "error": f"GitHub returned {ur.status_code}"}
        user = ur.json()
        rr = s.get("https://api.github.com/user/repos", timeout=10, params={
            "per_page": 100, "sort": "updated", "affiliation": "owner,collaborator",
        })
        repos = [r["full_name"] for r in (rr.json() if rr.ok else []) if isinstance(r, dict)]
        return {"ok": True, "user": user.get("login", ""), "name": user.get("name", ""), "repos": repos}
    except _req.RequestException as e:
        return {"ok": False, "error": str(e)}


def _setup_validate_jira(url: str, email: str, token: str) -> dict:
    try:
        auth = base64.b64encode(f"{email}:{token}".encode()).decode()
        s = _req.Session()
        s.headers.update({"Authorization": f"Basic {auth}", "Accept": "application/json"})
        base = url.rstrip("/")
        me = s.get(f"{base}/rest/api/3/myself", timeout=10)
        if me.status_code == 401:
            return {"ok": False, "error": "Auth failed (401) — check email and API token"}
        if me.status_code == 404:
            return {"ok": False, "error": "URL not found — is this the right Jira base URL?"}
        if not me.ok:
            return {"ok": False, "error": f"Jira returned {me.status_code}"}
        me_data = me.json()
        pr = s.get(f"{base}/rest/api/3/project", timeout=10, params={"maxResults": 100})
        projects = [{"key": p["key"], "name": p["name"]} for p in (pr.json() if pr.ok else [])]
        return {
            "ok": True,
            "account_id": me_data.get("accountId", ""),
            "display_name": me_data.get("displayName", ""),
            "email": me_data.get("emailAddress", ""),
            "projects": projects,
        }
    except _req.RequestException as e:
        return {"ok": False, "error": str(e)}


def _setup_jira_statuses(url: str, email: str, token: str, project: str) -> dict:
    try:
        auth = base64.b64encode(f"{email}:{token}".encode()).decode()
        s = _req.Session()
        s.headers.update({"Authorization": f"Basic {auth}", "Accept": "application/json"})
        base = url.rstrip("/")
        resp = s.get(f"{base}/rest/api/3/project/{project}/statuses", timeout=10)
        if not resp.ok:
            return {"ok": False, "error": f"Could not fetch statuses ({resp.status_code})"}
        names_set: set[str] = set()
        for issue_type in resp.json():
            for st in issue_type.get("statuses", []):
                names_set.add(st["name"])
        names = sorted(names_set)

        def find(*keywords: str) -> str | None:
            for kw in keywords:
                for n in names:
                    if kw.lower() in n.lower():
                        return n
            return None

        mapped = {
            "JIRA_STATUS_TODO":           find("to do", "todo", "backlog", "open") or (names[0] if names else "To Do"),
            "JIRA_STATUS_QUEUED":         find("queue") or "Queued",
            "JIRA_STATUS_IN_PROGRESS":    find("progress", "doing", "started", "in dev", "active") or "In Progress",
            "JIRA_STATUS_NEEDS_DECISION": find("decision", "needs decision", "waiting", "blocked by") or "Needs Decision",
            "JIRA_STATUS_BLOCKED":        find("block", "impede", "on hold") or "Blocked",
            "JIRA_STATUS_IN_REVIEW":      find("review", "pr open", "testing", "qa") or "In Review",
            "JIRA_STATUS_DONE":           find("done", "closed", "complete", "resolved", "released") or "Done",
        }
        return {"ok": True, "names": names, "mapped": mapped}
    except _req.RequestException as e:
        return {"ok": False, "error": str(e)}

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
            auth = self.headers.get("Authorization", "")
            if auth.startswith("Bearer ") and _check_token(token, auth[7:].strip()):
                return True
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            req_token = (params.get("token") or [""])[0]
            if req_token and _check_token(token, req_token):
                return True
            return False

        def _setup_authed(self) -> bool:
            """Setup routes bypass auth if no token is set yet (first-time setup)."""
            if not token:
                return True
            return self._authed()

        def _read_body(self) -> dict:
            length = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(length) or b"{}")

        def do_GET(self):  # noqa: N802
            if self.path in ("/healthz", "/health"):
                self._send(200, b'{"status":"ok"}', "application/json")
                return
            path = urlparse(self.path).path
            # Setup routes: accessible without auth if no token configured yet.
            if path in ("/setup", "/setup/"):
                if not self._setup_authed():
                    self._send(401, b'{"error":"unauthorized"}', "application/json")
                    return
                try:
                    with open(_SETUP_HTML_PATH, encoding="utf-8") as fh:
                        setup_html = fh.read()
                except OSError:
                    setup_html = "<h1>setup.html missing</h1>"
                self._send(200, setup_html.encode(), "text/html; charset=utf-8")
                return
            if path == "/api/setup/load":
                if not self._setup_authed():
                    self._send(401, b'{"error":"unauthorized"}', "application/json")
                    return
                self._json(_setup_load_env())
                return
            if not self._authed():
                self._send(401, b'{"error":"unauthorized"}', "application/json")
                return
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
            path = urlparse(self.path).path
            # Setup mutation routes — same auth rules as setup GET.
            if path.startswith("/api/setup/"):
                if not self._setup_authed():
                    self._send(401, b'{"error":"unauthorized"}', "application/json")
                    return
                try:
                    payload = self._read_body()
                except Exception:
                    self._json({"ok": False, "error": "invalid JSON body"}, code=400)
                    return
                if path == "/api/setup/validate/github":
                    self._json(_setup_validate_github(payload.get("token", "")))
                elif path == "/api/setup/validate/jira":
                    self._json(_setup_validate_jira(
                        payload.get("url", ""), payload.get("email", ""), payload.get("token", "")))
                elif path == "/api/setup/jira-statuses":
                    self._json(_setup_jira_statuses(
                        payload.get("url", ""), payload.get("email", ""),
                        payload.get("token", ""), payload.get("project", "")))
                elif path == "/api/setup/save":
                    self._json(_setup_save_env(payload))
                else:
                    self._send(404, b"not found", "text/plain")
                return
            if not self._authed():
                self._send(401, b'{"error":"unauthorized"}', "application/json")
                return
            if not path.startswith("/api/action"):
                self._send(404, b"not found", "text/plain")
                return
            try:
                payload = self._read_body()
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
