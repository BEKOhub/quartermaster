"""Typed settings loaded from the environment (.env in dev, vault in prod).

Only this module reads raw env vars. The Secrets Broker is the only component
that should touch the credential fields; everything else takes Settings and asks
the broker to act, so Claude never sees a key.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- run mode ---------------------------------------------------------
    mock_mode: bool = Field(default=True, alias="MOCK_MODE")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    # Artificial per-stage delay in mock mode so the dashboard visibly animates.
    mock_step_delay_seconds: float = Field(default=0.0, alias="MOCK_STEP_DELAY_SECONDS")

    # --- anthropic / claude ----------------------------------------------
    claude_code_oauth_token: str = Field(default="", alias="CLAUDE_CODE_OAUTH_TOKEN")
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    claude_bin: str = Field(default="claude", alias="CLAUDE_BIN")
    claude_model_plan: str = Field(default="haiku", alias="CLAUDE_MODEL_PLAN")
    claude_model_implement: str = Field(default="sonnet", alias="CLAUDE_MODEL_IMPLEMENT")
    claude_model_review: str = Field(default="sonnet", alias="CLAUDE_MODEL_REVIEW")
    claude_model_hard: str = Field(default="opus", alias="CLAUDE_MODEL_HARD")

    # --- jira -------------------------------------------------------------
    jira_base_url: str = Field(default="", alias="JIRA_BASE_URL")
    jira_agent_email: str = Field(default="", alias="JIRA_AGENT_EMAIL")
    jira_api_token: str = Field(default="", alias="JIRA_API_TOKEN")
    jira_project_key: str = Field(default="DEMO", alias="JIRA_PROJECT_KEY")
    jira_boss_account_id: str = Field(default="", alias="JIRA_BOSS_ACCOUNT_ID")

    jira_status_todo: str = Field(default="To Do", alias="JIRA_STATUS_TODO")
    jira_status_queued: str = Field(default="Queued", alias="JIRA_STATUS_QUEUED")
    jira_status_in_progress: str = Field(default="In Progress", alias="JIRA_STATUS_IN_PROGRESS")
    jira_status_needs_decision: str = Field(default="Needs Decision", alias="JIRA_STATUS_NEEDS_DECISION")
    jira_status_blocked: str = Field(default="Blocked", alias="JIRA_STATUS_BLOCKED")
    jira_status_in_review: str = Field(default="In Review", alias="JIRA_STATUS_IN_REVIEW")
    jira_status_done: str = Field(default="Done", alias="JIRA_STATUS_DONE")

    # --- github -----------------------------------------------------------
    gh_token: str = Field(default="", alias="GH_TOKEN")
    github_repo: str = Field(default="", alias="GITHUB_REPO")
    github_base_branch: str = Field(default="main", alias="GITHUB_BASE_BRANCH")
    github_branch_prefix: str = Field(default="agent/", alias="GITHUB_BRANCH_PREFIX")
    repo_path: str = Field(default="/workspace/repo", alias="REPO_PATH")
    worktrees_path: str = Field(default="/workspace/worktrees", alias="WORKTREES_PATH")

    # --- cloudflare / stripe / azure (held, least-privilege) --------------
    cloudflare_api_token: str = Field(default="", alias="CLOUDFLARE_API_TOKEN")
    cloudflare_account_id: str = Field(default="", alias="CLOUDFLARE_ACCOUNT_ID")
    stripe_api_key: str = Field(default="", alias="STRIPE_API_KEY")
    stripe_mode: str = Field(default="test", alias="STRIPE_MODE")
    azure_tenant_id: str = Field(default="", alias="AZURE_TENANT_ID")
    azure_client_id: str = Field(default="", alias="AZURE_CLIENT_ID")
    azure_client_secret: str = Field(default="", alias="AZURE_CLIENT_SECRET")
    azure_subscription_id: str = Field(default="", alias="AZURE_SUBSCRIPTION_ID")

    # --- queue ------------------------------------------------------------
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    queue_namespace: str = Field(default="quartermaster", alias="QUEUE_NAMESPACE")
    worker_concurrency: int = Field(default=1, alias="WORKER_CONCURRENCY")
    job_visibility_timeout: int = Field(default=1800, alias="JOB_VISIBILITY_TIMEOUT")
    job_max_attempts: int = Field(default=3, alias="JOB_MAX_ATTEMPTS")

    # --- poller -----------------------------------------------------------
    poll_interval_seconds: int = Field(default=120, alias="POLL_INTERVAL_SECONDS")

    # --- dashboard --------------------------------------------------------
    dashboard_enabled: bool = Field(default=True, alias="DASHBOARD_ENABLED")
    dashboard_port: int = Field(default=8000, alias="DASHBOARD_PORT")

    # --- budget -----------------------------------------------------------
    budget_db_path: str = Field(default="/workspace/data/ledger.db", alias="BUDGET_DB_PATH")
    budget_per_ticket_usd: float = Field(default=3.0, alias="BUDGET_PER_TICKET_USD")
    budget_monthly_usd: float = Field(default=90.0, alias="BUDGET_MONTHLY_USD")
    implement_max_retries: int = Field(default=2, alias="IMPLEMENT_MAX_RETRIES")

    # --- audit / build ----------------------------------------------------
    audit_log_path: str = Field(default="/workspace/data/audit.log", alias="AUDIT_LOG_PATH")
    build_test_command: str = Field(default='echo "no tests configured"', alias="BUILD_TEST_COMMAND")

    # --- observability ----------------------------------------------------
    runs_db_path: str = Field(default="/workspace/data/runs.db", alias="RUNS_DB_PATH")
    otel_enabled: bool = Field(default=False, alias="OTEL_ENABLED")

    # --- reliability (Tier 1) ---------------------------------------------
    review_votes: int = Field(default=3, alias="REVIEW_VOTES")           # adversarial panel size
    review_max_repairs: int = Field(default=2, alias="REVIEW_MAX_REPAIRS")  # review->implement loops
    escalate_model_on_retry: bool = Field(default=True, alias="ESCALATE_MODEL_ON_RETRY")
    acceptance_gate: bool = Field(default=True, alias="ACCEPTANCE_GATE")

    # --- security (Tier 3) ------------------------------------------------
    scan_diffs: bool = Field(default=True, alias="SCAN_DIFFS")
    canary_token: str = Field(default="QM-CANARY-do-not-exfil", alias="CANARY_TOKEN")

    # --- context/cost (Tier 4) --------------------------------------------
    repo_map_enabled: bool = Field(default=True, alias="REPO_MAP_ENABLED")
    repo_map_max_files: int = Field(default=400, alias="REPO_MAP_MAX_FILES")

    # --- notifications / role (Tier 2 / Tier 5) ---------------------------
    notify_webhook_url: str = Field(default="", alias="NOTIFY_WEBHOOK_URL")
    role: str = Field(default="all", alias="ROLE")  # all | poller | worker | dashboard

    def model_for_stage(self, stage: str, hard: bool = False) -> str:
        """Pick the CLI --model for a pipeline stage, honouring a hard/arch tag."""
        if hard:
            return self.claude_model_hard
        return {
            "plan": self.claude_model_plan,
            "implement": self.claude_model_implement,
            "review": self.claude_model_review,
        }.get(stage, self.claude_model_implement)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached singleton so the env is parsed once per process."""
    return Settings()
