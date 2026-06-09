"""Wire up the broker with real or mock connectors based on MOCK_MODE."""
from __future__ import annotations

from ..audit import AuditLog
from ..config import Settings
from .broker import SecretsBroker


def build_broker(settings: Settings, audit: AuditLog) -> SecretsBroker:
    if settings.mock_mode:
        from .connectors.mock import MockGitHubConnector, MockJiraConnector
        jira = MockJiraConnector()
        github = MockGitHubConnector()
    else:
        from .connectors.github import RealGitHubConnector
        from .connectors.jira import RealJiraConnector
        jira = RealJiraConnector(settings)
        github = RealGitHubConnector(settings)
    return SecretsBroker(settings, audit, jira, github)
