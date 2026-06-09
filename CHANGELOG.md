# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
