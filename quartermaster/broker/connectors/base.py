"""Connector interfaces. Real connectors hold credentials and call external
services; mock connectors fake them so the whole loop runs offline.

These define the *only* operations the agent is allowed to request. Anything not
here simply does not exist as far as Claude is concerned.
"""
from __future__ import annotations

from typing import Optional, Protocol

from ...models import Ticket


class JiraConnector(Protocol):
    def search_todo(self) -> list[Ticket]: ...
    def board(self) -> list[Ticket]: ...
    def get_ticket(self, key: str) -> Optional[Ticket]: ...
    def comment(self, key: str, body: str) -> None: ...
    def transition(self, key: str, status_name: str) -> None: ...
    def assign(self, key: str, account_id: str) -> None: ...


class GitHubConnector(Protocol):
    def open_pr(self, *, branch: str, title: str, body: str, base: str) -> str: ...
