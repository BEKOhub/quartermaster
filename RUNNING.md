# Running Quartermaster

## TL;DR — run it locally in mock mode (no accounts, no tokens)

```bash
cp example.env .env        # MOCK_MODE=true is the default
docker compose up --build
```

Open the **dashboard at http://localhost:8000** — a board with a column per status
and live indicator cards (spend, queue, token/cache-hit, pipeline, "needs you"),
clickable per-ticket run timelines, action buttons, notifications, and an audit
feed. With `MOCK_STEP_DELAY_SECONDS=3` you can watch the demo tickets move across
the columns in real time.

You'll see the whole loop run against six seeded demo tickets:

- **DEMO-1 / DEMO-2** → plan → implement → review → **PR opened**.
- **DEMO-3** (`arch` label) → planner gates → **ADR posted → Needs Decision**.
- **DEMO-4** (`reviewfail`) → reviewers reject → **repair loop** → **PR opened**.
- **DEMO-5** (`inject`) → poisoned diff → **caught by the scanner → Blocked**.
- **DEMO-6** (`structural`) → reviewer finds a structural problem → **escalated**.

In mock mode the issue tracker, GitHub, and the `claude -p` CLI are all faked, so
nothing external is touched and no tokens are spent.

## Tests & evals

```bash
# in a container (no local Python setup needed):
docker run --rm -v "$PWD":/app -w /app python:3.12-slim \
  bash -lc "pip install -q -e '.[dev]' && python -m pytest -q"

# the eval scorecard (regression + red-team):
docker run --rm -v "$PWD":/app -w /app python:3.12-slim \
  bash -lc "pip install -q -e '.' && python -m quartermaster.evals"
```

Or locally with a venv: `make install && make test && make evals`.

## Scale-out (distributed profile)

Run the poller, workers, and dashboard as separate services and scale workers
horizontally over the shared Redis queue:

```bash
docker compose --profile distributed up --build --scale worker=3
```

Run **exactly one** poller. Don't run the default `agent` service and the
distributed profile at the same time.

## Going live (`MOCK_MODE=false`)

1. **Fill `.env`** — every key is documented in `example.env` (Claude, Jira,
   GitHub, optional Cloudflare/Stripe/Cloud, Redis, budget caps, observability).
2. **Auth Claude headless:** `claude setup-token` on your laptop → put the token in
   `CLAUDE_CODE_OAUTH_TOKEN` (uses your Max plan's automated credit pool), or set
   `ANTHROPIC_API_KEY`.
3. **Mount your repo** into the container — uncomment the bind in
   `docker-compose.yml` (`- /path/to/your-repo:/workspace/repo`) and copy the
   `.claude/` config set into that repo so each `claude -p` run inherits the rules.
4. **Jira:** create a dedicated agent account + API token, set up the workflow
   statuses (names must match the `JIRA_STATUS_*` vars), and set
   `JIRA_BOSS_ACCOUNT_ID`.
5. **GitHub:** a fine-grained PAT scoped to the repo (contents R/W, PRs write). Turn
   on branch protection on `main` (required review = you) — the agent opens PRs,
   never merges.
6. Set `BUILD_TEST_COMMAND` to your real test/lint command.
7. `docker compose up --build`.

## Safety model

- **Claude has no keys and no network.** It only edits files in a worktree and runs
  tests. A prompt-injection ticket has nothing to leak.
- **The broker enforces least privilege** (`quartermaster/broker/policy.py`):
  issue-tracker read/comment/transition/assign, GitHub PR-on-feature-branch only,
  read/propose for infra, no live money writes. One-way doors (prod deploy,
  DNS/WAF, live billing) are DENY or PROPOSE→human-approves.
- **Every brokered call is audited** to `AUDIT_LOG_PATH` (append-only JSON lines).
- **The diff is scanned** for secrets / network calls / a canary token before any
  PR (`SCAN_DIFFS`).
- **Hooks** (`.claude/hooks/guard.py`) block edits to `.env`, pushes to `main`, and
  direct network calls inside the Claude sandbox.
- **Budget caps** park runaway tickets in Blocked; the monthly kill-switch stops new
  intake before the credit pool drains.

See [SECURITY.md](SECURITY.md) and [ARCHITECTURE.md](ARCHITECTURE.md).
