# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Dashboard authentication** — set `DASHBOARD_TOKEN` to require a bearer token
  on all dashboard endpoints; constant-time comparison; `/healthz` always public.
- **Scanner severity levels** — every finding now carries a `Severity` enum
  (`high`/`medium`/`low`). New patterns: Azure Storage connection strings, JWT
  tokens, GitHub OAuth/App tokens, bash TCP redirect (`/dev/tcp/...`). Generic
  credential regex tightened (≥16-char value required) to reduce false positives.
- **Queue dedup race fix** — `enqueue()` now uses Redis `WATCH/MULTI/EXEC`
  (optimistic locking) so concurrent workers can never claim the same ticket.
- **DLQ processing** — `queue.process_dlq()` method added and wired into the
  poller loop so dead-lettered jobs surface as warnings rather than accumulating.
- **Jira pagination** — `search_todo` and `board` now paginate automatically;
  projects with >50 tickets are no longer silently truncated.
- **GitHub idempotent PR** — `open_pr` detects an existing open PR for the branch
  and updates it instead of crashing on duplicate branch. New `update_pr()` and
  `close_pr()` operations.
- **Poller circuit breaker** — exponential backoff after consecutive failures
  (threshold 3, max 8× interval) so a broken Jira/Redis isn't hammered every cycle.
- **Observability**: `cache_write_tokens` now recorded per stage (was silently
  dropped); `ts` index added; `prune(keep_days)` method for retention.
- **Audit log durability** — `fsync` on every write; size-based rotation (default
  50 MB, 3 backups kept).
- **Config**: new variables `CLAUDE_TIMEOUT_SECONDS`, `BUILD_OUTPUT_MAX_CHARS`,
  `RUNS_RETENTION_DAYS`, `DASHBOARD_TOKEN`.

### Changed
- **Worker**: bare `except Exception` replaced with specific pass-through for
  `KeyboardInterrupt`/`SystemExit`; dead-letter recovery now handles each step
  (comment / set_status / assign) independently so a partial failure doesn't
  swallow the whole escalation; idle backoff has per-worker jitter.
- **Connectors**: Jira and GitHub both use an `_with_retry()` helper with 3-attempt
  exponential backoff for 429/5xx/network errors.
- **Reaper**: re-queued jobs now get a score jitter so reaped tickets don't all
  pile up at the same priority tier.
- **RepoMap**: cache signature changed from `(count, max_mtime)` to a sorted
  `(path, mtime)` SHA-1 hash — file deletions now correctly invalidate the cache.
  Symbol extraction distinguishes `FileNotFoundError` from timeout.
- **Worktree**: branch-already-exists handled on create; falls back to `rmtree`
  when `git worktree remove` fails; origin remote verified before push.
- **Notifier**: `sent` buffer changed from `list` to a bounded `deque`; webhook
  timeout and retry count now configurable; transient errors retried with backoff.
- **SSE stream**: replaced the hard 20-minute cap with an infinite loop + 30-second
  keep-alive comments so browser connections survive long-lived agent runs.

## [0.1.0] - 2026-06-09

Initial public release.

### Added
- Deterministic, token-free control plane: intake poller, Redis job queue
  (priority + FIFO + reliable in-flight + dead-letter + reaper), concurrency-capped
  worker pool, and a state machine.
- **Secrets Broker + Tool Gateway** — the only holder of credentials, with a
  per-service ALLOW / PROPOSE / DENY policy and an append-only audit log. Jira and
  GitHub connectors (real + mock).
- **Pipeline**: plan → implement → build/test → scan → adversarial review → PR,
  with an architecture-decision escalation gate, a review→repair loop, an
  acceptance-criteria gate, and model-tier escalation on retry.
- **Security**: diff scanner (secrets / network / canary token) and Claude Code
  guard hooks blocking `.env`, `main` pushes, and direct network calls.
- **Observability**: SQLite run history with per-stage cost/token/duration, an
  optional OpenTelemetry GenAI-span exporter, and a 6-scenario eval scorecard
  (`make evals`).
- **Cost control**: budget ledger with per-ticket + monthly caps and a kill-switch,
  cached repo map, prompt-cache + token tracking, and model routing.
- **Dashboard**: a dependency-free web UI (board + indicator cards + per-ticket run
  timelines + answer-ADR/re-queue/approve actions + notifications + SSE).
- **Scale**: role split (`poller` / `worker` / `dashboard`) and a distributed
  Docker Compose profile with horizontal worker scaling.
- Full mock mode so the entire loop runs locally with no accounts or API keys.
- 42 unit tests and a Docker/Compose stack.

[Unreleased]: https://github.com/BEKOhub/quartermaster/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/BEKOhub/quartermaster/releases/tag/v0.1.0
