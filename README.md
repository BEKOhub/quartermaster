<div align="center">

# 🛰️ Quartermaster

**An autonomous, secure coding agent that turns issue-tracker tickets into reviewed pull requests — driven by the Claude CLI, with a Secrets Broker so the model never holds a key.**

[![CI](https://github.com/BEKOhub/quartermaster/actions/workflows/ci.yml/badge.svg)](https://github.com/BEKOhub/quartermaster/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/docker-compose-2496ED.svg)](docker-compose.yml)

</div>

You write a ticket. Quartermaster picks it up, **plans** it, gates on any
architecture decision (escalating to you), **implements** it in an isolated git
worktree, runs an **adversarial review panel**, **scans the diff** for leaked
secrets, and opens a **pull request** — reporting every status and cost back to
the ticket. You stay the boss: you approve every design decision and merge every PR.

Built to run on a small always-on box (a mini PC, a VPS, a Pi-class machine).
The heavy lifting happens in the cloud via the Claude CLI; the box runs a
deterministic, zero-token control plane.

![dashboard](docs/img/dashboard.png)

---

## Why it's different

- **The model never holds a credential.** A **Secrets Broker** is the only
  component with keys; it brokers every external call under a least-privilege
  policy and an append-only audit log. Claude runs in a sandbox with no network —
  so a prompt-injected ticket has nothing to leak. ([SECURITY.md](SECURITY.md))
- **Deterministic orchestrator, rented intelligence.** The "manager" is plain code
  (zero tokens): a poller, a Redis queue, a budget ledger, a state machine. Claude
  is called in short, single-purpose bursts (plan / implement / review).
- **Verification-first.** An N-vote adversarial review panel, a review→repair
  loop, acceptance-criteria gates, and a diff security scanner — quality comes from
  scaffolding, not from trusting one model call.
- **Observable.** Every stage is recorded (model, verdict, cost, tokens, duration)
  with an optional OpenTelemetry export, a live web dashboard, and a regression
  **eval scorecard**.
- **You own every architecture decision.** The planner stops and writes an ADR for
  any structural choice; no code is written until you answer.

---

## Quickstart — no accounts, no API keys

```bash
git clone https://github.com/BEKOhub/quartermaster.git
cd quartermaster
cp example.env .env            # MOCK_MODE=true is the default
docker compose up --build
```

Open the dashboard at **http://localhost:8000**. In mock mode the issue tracker,
GitHub, and the Claude CLI are all faked, so you can watch the full loop run
against six demo tickets — including an architecture escalation, a review→repair
loop, and a prompt-injection attempt getting blocked.

| Demo ticket | What you see |
|---|---|
| DEMO-1, DEMO-2 | Happy path → plan → implement → review → **PR opened** |
| DEMO-3 (`arch`) | Planner gates → **ADR posted → Needs Decision** |
| DEMO-4 (`reviewfail`) | Reviewers reject → **repair loop** → Opus → PR |
| DEMO-5 (`inject`) | Poisoned diff → **scanner blocks → Blocked** |
| DEMO-6 (`structural`) | Structural flaw → **escalated to you** |

Run the eval scorecard:

```bash
make evals      # 6 golden scenarios incl. a red-team injection case
```

---

## How it works

```
  YOU (issue tracker)            Quartermaster (your box)              Claude API
─────────────────────────────────────────────────────────────────────────────────
 write ticket ───poll──►  Poller → Redis queue → Worker pool
 assignee = agent                     │
                                       ▼  per ticket (one Claude session):
                         ① PLAN ──► architecture decision? ──► ADR → assign YOU (stop)
                         ② IMPLEMENT (isolated worktree) ──► build/test (det.)
                         ③ SCAN diff (secrets / network / canary)
                         ④ REVIEW panel (N adversarial votes) ──► repair loop
                                       │
   review + merge ◄──── PR opened ◄────┘   every step: cost + status to the ticket
```

All external I/O (read ticket, comment, transition, assign, open PR) goes through
the **Secrets Broker**. The agent never merges to `main`.

---

## Features

| Area | What you get |
|------|--------------|
| **Reliability** | Adversarial N-vote review · review→repair loop · acceptance-criteria gate · model escalation (Sonnet→Opus on retry) |
| **Security** | Secrets Broker (no keys to the model) · per-service DENY/PROPOSE policy · diff scanner (secrets, network, canary, Azure, JWT, bash-TCP) · guard hooks |
| **Resilience** | Exponential backoff on Jira/GitHub errors · WATCH/MULTI/EXEC queue dedup · dead-letter processing · poller circuit breaker |
| **Observability** | SQLite run history · optional OpenTelemetry GenAI spans · live dashboard with per-ticket timelines · eval scorecard |
| **Cost control** | Budget ledger (per-ticket + monthly caps + kill-switch) · cached repo map · prompt-cache + full token tracking (read + write) · model routing |
| **Scale** | Redis queue with priority + DLQ · role split (`poller`/`worker`/`dashboard`) · horizontal worker scaling |
| **Human-in-the-loop** | Jira-style board · answer-ADR / re-queue / approve buttons · optional dashboard auth · notifications (Slack/Telegram webhook) |

See **[FEATURES.md](FEATURES.md)** for screenshots and details.

---

## Going live (real Jira + GitHub + Claude)

### 1. Jira

Create a dedicated agent Jira account and generate an API token at
https://id.atlassian.com/manage-profile/security/api-tokens.

Your workflow must have these statuses (names are configurable via `JIRA_STATUS_*`):
`To Do` · `Queued` · `In Progress` · `Needs Decision` · `Blocked` · `In Review` · `Done`

To start a ticket: assign it to the agent and move it to `To Do`.

### 2. GitHub

Create a **fine-grained PAT** scoped to your target repo:
- Permissions: **Contents** (R/W), **Pull requests** (W)

Enable branch protection on `main` (required review = you). The agent opens PRs, never merges.

### 3. Claude (headless)

```bash
# On your laptop (not in Docker):
claude setup-token
# Copy the printed token → CLAUDE_CODE_OAUTH_TOKEN in .env
```

Or use an `ANTHROPIC_API_KEY` from [console.anthropic.com](https://console.anthropic.com).

### 4. Fill `.env`

```env
MOCK_MODE=false

# Claude
CLAUDE_CODE_OAUTH_TOKEN=<token from setup-token>
# ANTHROPIC_API_KEY=sk-ant-...   # alternative

# Jira
JIRA_BASE_URL=https://your-site.atlassian.net
JIRA_AGENT_EMAIL=agent@yourdomain.com
JIRA_API_TOKEN=<api token>
JIRA_PROJECT_KEY=PROJ
JIRA_BOSS_ACCOUNT_ID=<your Jira account ID>

# GitHub
GH_TOKEN=<fine-grained PAT>
GITHUB_REPO=your-org/your-repo

# Build/test command run after every implementation (non-zero = fail → retry)
BUILD_TEST_COMMAND=pytest -q

# Budget
BUDGET_PER_TICKET_USD=3
BUDGET_MONTHLY_USD=90

# Optional: protect the dashboard
DASHBOARD_TOKEN=a-secret-token
```

Every variable is documented in [`example.env`](example.env).

### 5. Mount your repo

In `docker-compose.yml`, uncomment:

```yaml
- /path/to/your-repo:/workspace/repo
```

Copy the agent config into the repo so each `claude -p` run inherits the rules:

```bash
cp -r .claude/ /path/to/your-repo/.claude/
```

### 6. Start

```bash
docker compose up --build
```

Full walkthrough: **[RUNNING.md](RUNNING.md)**.

---

## Daily workflow

1. **Write a Jira ticket** — summary = title, description = what you want done.
2. **Assign it to the agent** and move it to `To Do`.
3. Quartermaster picks it up within 2 minutes and comments `📥 Queued`.
4. If it hits an architecture decision → it stops, assigns the ticket back to you with an ADR.
5. You reply in Jira comments, move it back to `To Do` → it resumes.
6. Otherwise → it implements, scans, reviews, opens a PR → ticket moves to `In Review`.
7. **You review and merge the PR.**

Every step posts a comment to the ticket with its verdict and running cost.

---

## Dashboard

Open **http://localhost:8000** (or `DASHBOARD_PORT`).

If `DASHBOARD_TOKEN` is set, authenticate via:
- Query param: `http://localhost:8000?token=your-token`
- Header: `Authorization: Bearer your-token`

The health check at `/healthz` is always unauthenticated.

---

## Configuration reference

Key variables (full list in [`example.env`](example.env)):

| Variable | Default | Purpose |
|---|---|---|
| `MOCK_MODE` | `true` | Fake all external I/O for local testing |
| `CLAUDE_CODE_OAUTH_TOKEN` | | Headless Claude auth (preferred) |
| `ANTHROPIC_API_KEY` | | Pay-as-you-go alternative |
| `CLAUDE_TIMEOUT_SECONDS` | `1800` | Per-stage Claude CLI timeout |
| `REVIEW_VOTES` | `3` | Adversarial panel size |
| `REVIEW_MAX_REPAIRS` | `2` | Max review→repair rounds |
| `BUDGET_PER_TICKET_USD` | `3.0` | Per-ticket spend cap |
| `BUDGET_MONTHLY_USD` | `90.0` | Monthly kill-switch cap |
| `BUILD_TEST_COMMAND` | `echo ...` | Command run after every implementation |
| `BUILD_OUTPUT_MAX_CHARS` | `3000` | Max chars of build output fed back to Claude |
| `SCAN_DIFFS` | `true` | Enable diff security scanner |
| `DASHBOARD_TOKEN` | | Protect the dashboard with a bearer token |
| `NOTIFY_WEBHOOK_URL` | | Slack/Telegram/Discord webhook for alerts |
| `RUNS_RETENTION_DAYS` | `90` | Auto-prune run history older than N days |
| `WORKER_CONCURRENCY` | `1` | Parallel tickets (keep low on a mini PC) |
| `ROLE` | `all` | `all` \| `poller` \| `worker` \| `dashboard` |

---

## Tech

Pure-Python control plane (stdlib + `redis`, `requests`, `pydantic`), a stdlib web
dashboard (no JS framework), Redis for the queue, SQLite for the ledger and run
history, Docker Compose for the stack. Drives the official **Claude Code CLI**.

---

## Status

Beta. Runs end-to-end today in mock mode; the real-mode connectors (Jira REST,
GitHub PRs) are implemented and the Cloudflare/Stripe/Azure policy slots are
stubbed for least-privilege brokering. Contributions welcome — see
[CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE) © Hamza BEKOURY
