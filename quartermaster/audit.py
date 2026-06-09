"""Append-only audit log of every brokered external call.

One JSON object per line. The broker writes here for service/operation/ticket/
args-summary/result/timestamp, so there is always a full trail of what the agent
asked an external service to do.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any

from .logging_setup import get_logger

log = get_logger("audit")


class AuditLog:
    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    def record(
        self,
        *,
        service: str,
        operation: str,
        ticket_key: str = "",
        args_summary: str = "",
        result: str = "",
        allowed: bool = True,
    ) -> None:
        entry: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "service": service,
            "operation": operation,
            "ticket": ticket_key,
            "args": args_summary,
            "result": result,
            "allowed": allowed,
        }
        line = json.dumps(entry, ensure_ascii=False)
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        level = log.info if allowed else log.warning
        level("AUDIT %s.%s ticket=%s allowed=%s %s", service, operation,
              ticket_key or "-", allowed, result)
