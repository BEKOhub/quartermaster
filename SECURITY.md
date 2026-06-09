# Security Policy

Quartermaster runs an autonomous agent that can edit code and open pull requests,
so security is a first-class design concern, not an add-on.

## The threat model

A ticket is **untrusted input**. The primary threat is **prompt injection** — a
ticket (or a file the agent reads) trying to make the model exfiltrate secrets,
call out to an attacker, or ship malicious code.

## Defenses in this project

1. **No keys in the model.** The Secrets Broker is the only component with
   credentials. Claude runs with **no network** and **no vault access** — it only
   edits files in an isolated git worktree and runs tests. There is nothing to leak.
2. **Capability allow-list.** Every external operation is classified
   ALLOW / PROPOSE / DENY per service (`quartermaster/broker/policy.py`). One-way
   doors (merge `main`, force-push, live billing, prod deploy, DNS/WAF) are denied
   or require human approval.
3. **Output scanning.** The diff is scanned for secrets, outbound-network calls,
   and a planted **canary token** before any PR (`quartermaster/scanner.py`). A hit
   blocks and escalates to a human.
4. **Guard hooks.** `.claude/hooks/guard.py` blocks edits to `.env`, pushes to
   `main`, and direct network calls (`curl`/`wget`) inside the sandbox.
5. **Audit trail.** Every brokered external call is written to an append-only log.
6. **Human in the loop.** The agent never merges; every architecture decision and
   every PR is approved by a person.
7. **Budget kill-switch.** Per-ticket and monthly caps stop a runaway loop.

## Hardening recommendations for production

- Run the Claude execution in a network-isolated sandbox (container with
  `network=none`, or gVisor/Firecracker) and apply an **egress allow-list** on the
  host (Anthropic + your issue tracker + GitHub only).
- Store secrets in an encrypted vault (sops/age or a secret manager), not a
  plaintext `.env`, and give the agent **scoped, least-privilege** tokens per
  service — never personal admin tokens.
- Keep production deploys and any live-money operations entirely out of the
  autonomous loop.

## Reporting a vulnerability

Please report security issues **privately**. Use GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
for this repository (Security → Report a vulnerability), or email the maintainer.

Please do **not** open a public issue for vulnerabilities. We aim to acknowledge
reports within a few days and will coordinate a fix and disclosure timeline with
you.
