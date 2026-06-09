import os
import tempfile

import pytest

# Force mock mode and isolated paths before settings are constructed.
os.environ.setdefault("MOCK_MODE", "true")
os.environ.setdefault("LOG_LEVEL", "WARNING")

from quartermaster.config import Settings  # noqa: E402


@pytest.fixture
def settings(tmp_path):
    return Settings(
        MOCK_MODE=True,
        REDIS_URL="redis://localhost:6379/15",
        QUEUE_NAMESPACE="test",
        BUDGET_DB_PATH=str(tmp_path / "ledger.db"),
        AUDIT_LOG_PATH=str(tmp_path / "audit.log"),
        RUNS_DB_PATH=str(tmp_path / "runs.db"),
        BUDGET_PER_TICKET_USD=3.0,
        BUDGET_MONTHLY_USD=90.0,
        IMPLEMENT_MAX_RETRIES=2,
        REPO_PATH=str(tmp_path / "repo"),
        WORKTREES_PATH=str(tmp_path / "wt"),
        JOB_MAX_ATTEMPTS=3,
    )


@pytest.fixture
def fake_queue(settings):
    import fakeredis
    from quartermaster.queue import JobQueue
    client = fakeredis.FakeStrictRedis(decode_responses=True)
    return JobQueue(settings.redis_url, settings.queue_namespace,
                    max_attempts=settings.job_max_attempts,
                    visibility_timeout=settings.job_visibility_timeout,
                    client=client)
