"""Deterministic session policy: one ticket = one canonical Claude session,
derived from the ticket key so no mapping store is needed.
"""
from __future__ import annotations

import uuid

from .models import SESSION_NAMESPACE


def session_id_for(ticket_key: str, *, variant: str = "") -> str:
    """uuid5(KEY) — stable across restarts. `variant` (e.g. 'v2') yields a fresh
    sub-session when a ticket's context grows stale or scope changes."""
    name = ticket_key if not variant else f"{ticket_key}-{variant}"
    return str(uuid.uuid5(SESSION_NAMESPACE, name))
