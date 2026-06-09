"""Notifier — pings you when a ticket needs the boss (ADR, blocked, or a PR to
review). Posts to a webhook (Slack/Telegram/Discord-compatible JSON) if
NOTIFY_WEBHOOK_URL is set; always logs. Best-effort, never raises into the
pipeline.
"""
from __future__ import annotations

import json
import urllib.request

from .logging_setup import get_logger

log = get_logger("notify")

_EMOJI = {"needs_decision": "🧭", "blocked": "⛔", "pr_opened": "✅"}


class Notifier:
    def __init__(self, webhook_url: str = "", *, mock_mode: bool = True) -> None:
        self.webhook_url = webhook_url
        self.mock_mode = mock_mode
        self.sent: list[dict] = []  # kept for tests / dashboard feed

    def send(self, *, ticket_key: str, kind: str, message: str) -> None:
        text = f"{_EMOJI.get(kind, '🔔')} {ticket_key}: {kind.replace('_', ' ')} — {message}"
        self.sent.append({"ticket": ticket_key, "kind": kind, "message": message})
        self.sent[:] = self.sent[-50:]
        log.info("NOTIFY %s", text)
        if self.webhook_url and not self.mock_mode:
            try:
                req = urllib.request.Request(
                    self.webhook_url, data=json.dumps({"text": text}).encode(),
                    headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=5).read()
            except Exception as e:  # noqa: BLE001
                log.warning("webhook post failed: %s", e)
