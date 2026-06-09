"""The Secrets Broker + Tool Gateway — the ONLY component with credentials.

Every external action goes through `_gate()`, which:
  1. looks up the per-service policy,
  2. denies / proposes / executes accordingly,
  3. writes an audit-log entry.

The pipeline and poller call the high-level helpers (transition, comment, etc.),
never the connectors or the network directly. Claude, running in its sandbox,
has no network at all and asks for nothing here — it only edits files in its
worktree. This is the prompt-injection firewall: a malicious ticket has no key
to leak and no allowed operation to abuse.
"""
from __future__ import annotations

from typing import Optional

from ..audit import AuditLog
from ..config import Settings
from ..logging_setup import get_logger
from ..models import Status, Ticket
from .connectors.base import GitHubConnector, JiraConnector
from .policy import Decision, decide

log = get_logger("broker")


class PolicyError(RuntimeError):
    """Raised when an operation is denied by policy."""


class SecretsBroker:
    def __init__(self, settings: Settings, audit: AuditLog,
                 jira: JiraConnector, github: GitHubConnector) -> None:
        self.s = settings
        self.audit = audit
        self._jira = jira
        self._github = github

    # --- gateway ----------------------------------------------------------
    def _gate(self, service: str, operation: str, *, ticket_key: str = "",
              args_summary: str = "") -> Decision:
        d = decide(service, operation)
        if d is Decision.DENY:
            self.audit.record(service=service, operation=operation,
                              ticket_key=ticket_key, args_summary=args_summary,
                              result="DENIED by policy", allowed=False)
            raise PolicyError(f"{service}.{operation} is denied by policy")
        if d is Decision.PROPOSE:
            self.audit.record(service=service, operation=operation,
                              ticket_key=ticket_key, args_summary=args_summary,
                              result="PROPOSED -> approval ticket", allowed=True)
        return d

    def _status_name(self, status: Status) -> str:
        from .connectors.jira import status_name
        return status_name(self.s, status)

    # --- Jira helpers (used by poller + pipeline) -------------------------
    def search_todo(self) -> list[Ticket]:
        self._gate("jira", "search")
        tickets = self._jira.search_todo()
        self.audit.record(service="jira", operation="search",
                          result=f"{len(tickets)} todo tickets")
        return tickets

    def board(self) -> list[Ticket]:
        """All tickets across every status — used by the dashboard."""
        self._gate("jira", "search")
        return self._jira.board()

    def get_ticket(self, key: str) -> Optional[Ticket]:
        self._gate("jira", "get", ticket_key=key)
        return self._jira.get_ticket(key)

    def comment(self, key: str, body: str) -> None:
        self._gate("jira", "comment", ticket_key=key, args_summary=body[:80])
        self._jira.comment(key, body)
        self.audit.record(service="jira", operation="comment", ticket_key=key,
                          args_summary=body[:80], result="posted")

    def set_status(self, key: str, status: Status) -> None:
        name = self._status_name(status)
        self._gate("jira", "transition", ticket_key=key, args_summary=name)
        self._jira.transition(key, name)
        self.audit.record(service="jira", operation="transition", ticket_key=key,
                          args_summary=name, result="transitioned")

    def assign_to_boss(self, key: str) -> None:
        self._gate("jira", "assign", ticket_key=key, args_summary="boss")
        self._jira.assign(key, self.s.jira_boss_account_id)
        self.audit.record(service="jira", operation="assign", ticket_key=key,
                          args_summary="boss", result="assigned to boss")

    def assign_to_agent(self, key: str) -> None:
        self._gate("jira", "assign", ticket_key=key, args_summary="agent")
        self._jira.assign(key, "agent" if self.s.mock_mode else self.s.jira_agent_email)
        self.audit.record(service="jira", operation="assign", ticket_key=key,
                          args_summary="agent", result="assigned to agent")

    # --- GitHub helper ----------------------------------------------------
    def open_pr(self, *, ticket_key: str, branch: str, title: str, body: str) -> str:
        self._gate("github", "open_pr", ticket_key=ticket_key, args_summary=branch)
        url = self._github.open_pr(branch=branch, title=title, body=body,
                                   base=self.s.github_base_branch)
        self.audit.record(service="github", operation="open_pr", ticket_key=ticket_key,
                          args_summary=branch, result=url)
        return url

    # --- approval gate for one-way doors ----------------------------------
    def request_approval(self, *, ticket_key: str, service: str, operation: str,
                         detail: str) -> None:
        """For PROPOSE/irreversible actions: never execute, file an approval
        ticket and hand it to the boss."""
        self._gate(service, operation, ticket_key=ticket_key, args_summary=detail[:80])
        body = (f"🔐 APPROVAL REQUIRED — {service}.{operation}\n\n{detail}\n\n"
                f"The agent will NOT execute this. A privileged human runner must.")
        self.comment(ticket_key, body)
        self.set_status(ticket_key, Status.NEEDS_DECISION)
        self.assign_to_boss(ticket_key)
