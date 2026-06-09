"""GitHub PR connector (real). The broker policy guarantees this can only open a
PR on a feature branch — never merge to main, force-push, or change settings.
"""
from __future__ import annotations

import requests

from ...config import Settings
from ...logging_setup import get_logger

log = get_logger("github")


class RealGitHubConnector:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {settings.gh_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })

    def open_pr(self, *, branch: str, title: str, body: str, base: str) -> str:
        url = f"https://api.github.com/repos/{self.s.github_repo}/pulls"
        resp = self.session.post(url, json={
            "title": title, "body": body, "head": branch, "base": base,
        })
        resp.raise_for_status()
        return resp.json()["html_url"]
