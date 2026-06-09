# Contributing to Quartermaster

Thanks for your interest! Contributions of all kinds are welcome — bug reports,
docs, tests, connectors, and features.

## Getting started

```bash
git clone https://github.com/REPLACE_ME/quartermaster.git
cd quartermaster
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
make test        # run the unit suite
make evals       # run the pipeline eval scorecard
```

Or run everything in Docker without a local Python setup:

```bash
cp example.env .env
docker compose up --build      # dashboard at http://localhost:8000
```

The whole system runs in **mock mode** (`MOCK_MODE=true`) with no external
accounts or API keys, so you can develop and test the full loop offline.

## Development principles

- **The control plane stays deterministic and token-free.** Orchestration is plain
  code; the only place that spends tokens is the Claude CLI roles.
- **The model never holds a credential.** All external I/O goes through the Secrets
  Broker. Don't add direct network calls to the pipeline or worker.
- **Verification first.** New behaviour should be covered by a unit test, and where
  it affects an end-to-end outcome, by a scenario in the eval harness
  (`quartermaster/evals.py`).
- **Keep tickets/PRs tightly scoped.** Small, focused changes are easier to review.

## Before you open a PR

1. `make test` passes (or the Docker equivalent in the README).
2. `make evals` is still 6/6.
3. New/changed behaviour has tests.
4. Code matches the surrounding style (comments explain *why*, not *what*).
5. The PR description explains the change and links any related issue.

## Adding a connector

External services live behind thin connectors in
`quartermaster/broker/connectors/` with a real and a mock implementation, plus an
entry in `broker/policy.py` classifying each operation ALLOW / PROPOSE / DENY.
Default to the least privilege that does the job.

## Reporting security issues

Please **do not** open a public issue for vulnerabilities — see
[SECURITY.md](SECURITY.md).

## Code of conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md). By
participating you agree to uphold it.

## License

By contributing you agree that your contributions are licensed under the
project's [MIT License](LICENSE).
