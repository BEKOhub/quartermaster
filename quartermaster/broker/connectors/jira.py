"""Jira Cloud REST connector (real + mock).

Real connector uses Basic auth (agent email + API token) against the Jira Cloud
v3 REST API. It looks up transition IDs by name at runtime, so you only need to
name the workflow transitions clearly — no hard-coded IDs.
"""
from __future__ import annotations

import base64
import time
from typing import Optional

import requests
from requests.exceptions import HTTPError, RequestException

from ...config import Settings
from ...logging_setup import get_logger
from ...models import Status, Ticket

log = get_logger("jira")

_RETRY_STATUSES = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3
_BACKOFF_BASE = 2.0  # seconds; doubled on each retry


def _with_retry(fn, *args, **kwargs):
    """Call fn(*args, **kwargs), retrying on transient HTTP/network errors."""
    delay = _BACKOFF_BASE
    for attempt in range(_MAX_RETRIES):
        try:
            resp = fn(*args, **kwargs)
            if resp.status_code in _RETRY_STATUSES:
                if attempt == _MAX_RETRIES - 1:
                    resp.raise_for_status()
                log.warning("jira %s attempt %s/%s — retrying in %.0fs",
                            resp.url, attempt + 1, _MAX_RETRIES, delay)
                time.sleep(delay)
                delay *= 2
                continue
            resp.raise_for_status()
            return resp
        except RequestException as exc:
            if attempt == _MAX_RETRIES - 1:
                raise
            log.warning("jira request error attempt %s/%s: %s — retrying in %.0fs",
                        attempt + 1, _MAX_RETRIES, exc, delay)
            time.sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")

# Map our logical Status -> the configured Jira status name.
def status_name(settings: Settings, status: Status) -> str:
    return {
        Status.TODO: settings.jira_status_todo,
        Status.QUEUED: settings.jira_status_queued,
        Status.IN_PROGRESS: settings.jira_status_in_progress,
        Status.NEEDS_DECISION: settings.jira_status_needs_decision,
        Status.BLOCKED: settings.jira_status_blocked,
        Status.IN_REVIEW: settings.jira_status_in_review,
        Status.DONE: settings.jira_status_done,
    }[status]


# Reverse: a Jira status name -> our logical Status (for the dashboard board).
def status_from_name(settings: Settings, name: str) -> Status:
    lookup = {
        settings.jira_status_todo.lower(): Status.TODO,
        settings.jira_status_queued.lower(): Status.QUEUED,
        settings.jira_status_in_progress.lower(): Status.IN_PROGRESS,
        settings.jira_status_needs_decision.lower(): Status.NEEDS_DECISION,
        settings.jira_status_blocked.lower(): Status.BLOCKED,
        settings.jira_status_in_review.lower(): Status.IN_REVIEW,
        settings.jira_status_done.lower(): Status.DONE,
    }
    return lookup.get((name or "").lower(), Status.TODO)


class RealJiraConnector:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        token = base64.b64encode(
            f"{settings.jira_agent_email}:{settings.jira_api_token}".encode()
        ).decode()
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Basic {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        self.base = settings.jira_base_url.rstrip("/")

    def _url(self, path: str) -> str:
        return f"{self.base}/rest/api/3{path}"

    def _paginated_search(self, jql: str, page_size: int = 50) -> list[Ticket]:
        """Fetch all matching issues across pages."""
        fields = ["summary", "description", "status", "assignee", "priority", "labels"]
        tickets, start_at = [], 0
        while True:
            resp = _with_retry(self.session.post, self._url("/search/jql"), json={
                "jql": jql, "maxResults": page_size, "startAt": start_at, "fields": fields,
            })
            data = resp.json()
            issues = data.get("issues", [])
            tickets.extend(self._to_ticket(i) for i in issues)
            start_at += len(issues)
            if start_at >= data.get("total", 0) or not issues:
                break
        return tickets

    def search_todo(self) -> list[Ticket]:
        jql = (
            f'project = "{self.s.jira_project_key}" '
            f'AND assignee = "{self.s.jira_agent_email}" '
            f'AND status = "{self.s.jira_status_todo}" ORDER BY priority DESC, created ASC'
        )
        return self._paginated_search(jql)

    def get_ticket(self, key: str) -> Optional[Ticket]:
        try:
            resp = _with_retry(self.session.get, self._url(f"/issue/{key}"))
        except HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                return None
            raise
        return self._to_ticket(resp.json())

    def board(self) -> list[Ticket]:
        """All tickets in the project (every status) for the dashboard."""
        jql = f'project = "{self.s.jira_project_key}" ORDER BY created DESC'
        return self._paginated_search(jql, page_size=100)

    def comment(self, key: str, body: str) -> None:
        payload = {
            "body": {
                "type": "doc", "version": 1,
                "content": [{"type": "paragraph",
                             "content": [{"type": "text", "text": body}]}],
            }
        }
        _with_retry(self.session.post, self._url(f"/issue/{key}/comment"), json=payload)

    def transition(self, key: str, status_name: str) -> None:
        resp = _with_retry(self.session.get, self._url(f"/issue/{key}/transitions"))
        transitions = resp.json().get("transitions", [])
        match = next((t for t in transitions
                      if t["to"]["name"].lower() == status_name.lower()
                      or t["name"].lower() == status_name.lower()), None)
        if not match:
            raise RuntimeError(f"no transition to '{status_name}' on {key}; "
                               f"available: {[t['name'] for t in transitions]}")
        _with_retry(self.session.post, self._url(f"/issue/{key}/transitions"),
                    json={"transition": {"id": match["id"]}})

    def assign(self, key: str, account_id: str) -> None:
        _with_retry(self.session.put, self._url(f"/issue/{key}/assignee"),
                    json={"accountId": account_id or None})

    def _to_ticket(self, issue: dict) -> Ticket:
        f = issue.get("fields", {})
        desc = f.get("description")
        if isinstance(desc, dict):  # ADF -> flatten to text
            desc = _adf_to_text(desc)
        status_obj = f.get("status") or {}
        return Ticket(
            key=issue["key"],
            summary=f.get("summary", ""),
            description=desc or "",
            status=status_from_name(self.s, status_obj.get("name", "")),
            assignee=(f.get("assignee") or {}).get("emailAddress", ""),
            priority=(f.get("priority") or {}).get("name", "Medium"),
            labels=f.get("labels", []) or [],
        )


def _adf_to_text(node: dict) -> str:
    parts: list[str] = []
    if node.get("type") == "text":
        parts.append(node.get("text", ""))
    for child in node.get("content", []) or []:
        parts.append(_adf_to_text(child))
    return " ".join(p for p in parts if p)
