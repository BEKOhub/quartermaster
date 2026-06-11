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

_DEFAULT_MAX_BYTES = 50 * 1024 * 1024  # 50 MB before rotation
_DEFAULT_KEEP_BACKUPS = 3


class AuditLog:
    def __init__(self, path: str, *,
                 max_bytes: int = _DEFAULT_MAX_BYTES,
                 keep_backups: int = _DEFAULT_KEEP_BACKUPS) -> None:
        self.path = path
        self.max_bytes = max_bytes
        self.keep_backups = keep_backups
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
            self._maybe_rotate()
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        level = log.info if allowed else log.warning
        level("AUDIT %s.%s ticket=%s allowed=%s %s", service, operation,
              ticket_key or "-", allowed, result)

    def _maybe_rotate(self) -> None:
        """Rotate audit.log -> audit.log.1 -> audit.log.2 ... when size exceeded.
        Must be called under self._lock.
        """
        try:
            if not os.path.exists(self.path):
                return
            if os.path.getsize(self.path) < self.max_bytes:
                return
            # Shift existing backups.
            for i in range(self.keep_backups - 1, 0, -1):
                src = f"{self.path}.{i}"
                dst = f"{self.path}.{i + 1}"
                if os.path.exists(src):
                    os.replace(src, dst)
            os.replace(self.path, f"{self.path}.1")
            log.info("audit log rotated (exceeded %s bytes)", self.max_bytes)
        except OSError as e:
            log.warning("audit log rotation failed: %s", e)
