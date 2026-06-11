"""GitHub PR connector (real). The broker policy guarantees this can only open a
PR on a feature branch — never merge to main, force-push, or change settings.
"""
from __future__ import annotations

import time

import requests
from requests.exceptions import HTTPError, RequestException

from ...config import Settings
from ...logging_setup import get_logger

log = get_logger("github")

_MAX_RETRIES = 3
_BACKOFF_BASE = 2.0
_RETRY_STATUSES = {429, 500, 502, 503, 504}


def _with_retry(fn, *args, **kwargs):
    """Retry on transient HTTP/network errors with exponential backoff."""
    delay = _BACKOFF_BASE
    for attempt in range(_MAX_RETRIES):
        try:
            resp = fn(*args, **kwargs)
            if resp.status_code in _RETRY_STATUSES:
                if attempt == _MAX_RETRIES - 1:
                    resp.raise_for_status()
                log.warning("github %s attempt %s/%s — retrying in %.0fs",
                            getattr(resp, "url", "?"), attempt + 1, _MAX_RETRIES, delay)
                time.sleep(delay)
                delay *= 2
                continue
            resp.raise_for_status()
            return resp
        except RequestException as exc:
            if attempt == _MAX_RETRIES - 1:
                raise
            log.warning("github request error attempt %s/%s: %s — retrying in %.0fs",
                        attempt + 1, _MAX_RETRIES, exc, delay)
            time.sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")


class RealGitHubConnector:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {settings.gh_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })

    def _repo_url(self, path: str) -> str:
        return f"https://api.github.com/repos/{self.s.github_repo}{path}"

    def open_pr(self, *, branch: str, title: str, body: str, base: str) -> str:
        """Open a PR; if one already exists for this branch, update and return it."""
        existing = self._find_open_pr(branch, base)
        if existing:
            log.info("PR already exists for branch %s — updating", branch)
            return self.update_pr(existing["number"], title=title, body=body)
        resp = _with_retry(self.session.post, self._repo_url("/pulls"), json={
            "title": title, "body": body, "head": branch, "base": base,
        })
        return resp.json()["html_url"]

    def update_pr(self, pr_number: int, *, title: str = "", body: str = "") -> str:
        """Update the title/body of an existing PR."""
        payload = {}
        if title:
            payload["title"] = title
        if body:
            payload["body"] = body
        resp = _with_retry(self.session.patch, self._repo_url(f"/pulls/{pr_number}"),
                           json=payload)
        return resp.json()["html_url"]

    def close_pr(self, pr_number: int) -> None:
        """Close a PR without merging."""
        _with_retry(self.session.patch, self._repo_url(f"/pulls/{pr_number}"),
                    json={"state": "closed"})

    def _find_open_pr(self, branch: str, base: str) -> dict | None:
        """Return the first open PR for this head branch, or None."""
        try:
            resp = _with_retry(self.session.get, self._repo_url("/pulls"), params={
                "head": f"{self.s.github_repo.split('/')[0]}:{branch}",
                "base": base, "state": "open",
            })
            prs = resp.json()
            return prs[0] if prs else None
        except HTTPError:
            return None
