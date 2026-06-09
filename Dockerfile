# Quartermaster — control plane image.
# Contains the deterministic Python orchestrator (poller + queue worker +
# Secrets Broker + budget ledger) and the Claude Code CLI it drives.
FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# System tools the controller shells out to: git for worktrees, jq/ripgrep for
# context, curl to install the Claude CLI, nodejs for the CLI runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates jq ripgrep tini nodejs npm \
    && rm -rf /var/lib/apt/lists/*

# Claude Code CLI (the `claude -p` worker). Pinned via npm for reproducibility.
RUN npm install -g @anthropic-ai/claude-code || \
    (curl -fsSL https://claude.ai/install.sh | bash) ; \
    true

WORKDIR /app

COPY requirements.txt requirements-dev.txt ./
RUN pip install -r requirements.txt

COPY pyproject.toml ./
COPY quartermaster ./quartermaster
COPY prompts ./prompts
RUN pip install -e .

# Runtime state lives here (mounted as a volume in compose).
RUN mkdir -p /workspace/data /workspace/worktrees /workspace/repo

# tini handles signals so the worker pool shuts down cleanly.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "quartermaster.main"]
