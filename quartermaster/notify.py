"""Notifier — pings you when a ticket needs the boss (ADR, blocked, or a PR to
review). Posts to a webhook (Slack/Telegram/Discord-compatible JSON) if
NOTIFY_WEBHOOK_URL is set; always logs. Best-effort, never raises into the
pipeline.
"""
from __future__ import annotations

import collections
import json
import time
import urllib.error
import urllib.request

from .logging_setup import get_logger

log = get_logger("notify")

_EMOJI = {"needs_decision": "🧭", "blocked": "⛔", "pr_opened": "✅"}
_WEBHOOK_TIMEOUT = 10       # seconds
_MAX_RETRIES = 2
_RETRY_BACKOFF = 2.0        # seconds between retries
_BUFFER_SIZE = 100


class Notifier:
    def __init__(self, webhook_url: str = "", *, mock_mode: bool = True) -> None:
        self.webhook_url = webhook_url
        self.mock_mode = mock_mode
        # Use a bounded deque so the buffer never exceeds _BUFFER_SIZE in memory.
        self.sent: collections.deque = collections.deque(maxlen=_BUFFER_SIZE)

    def send(self, *, ticket_key: str, kind: str, message: str) -> None:
        text = f"{_EMOJI.get(kind, '🔔')} {ticket_key}: {kind.replace('_', ' ')} — {message}"
        self.sent.append({"ticket": ticket_key, "kind": kind, "message": message})
        log.info("NOTIFY %s", text)
        if self.webhook_url and not self.mock_mode:
            self._post_with_retry(text)

    def _post_with_retry(self, text: str) -> None:
        payload = json.dumps({"text": text}).encode()
        delay = _RETRY_BACKOFF
        for attempt in range(_MAX_RETRIES + 1):
            try:
                req = urllib.request.Request(
                    self.webhook_url, data=payload,
                    headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=_WEBHOOK_TIMEOUT).read()
                return
            except urllib.error.HTTPError as e:
                if e.code < 500 or attempt == _MAX_RETRIES:
                    log.warning("webhook HTTP error %s (attempt %s): %s",
                                e.code, attempt + 1, e)
                    return
            except (urllib.error.URLError, OSError) as e:
                if attempt == _MAX_RETRIES:
                    log.warning("webhook post failed after %s attempts: %s",
                                _MAX_RETRIES + 1, e)
                    return
            log.debug("webhook transient error — retrying in %.0fs", delay)
            time.sleep(delay)
            delay *= 2
