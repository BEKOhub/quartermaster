---
name: implement-ticket
description: The per-ticket workflow for Quartermaster — plan, gate, implement, review, PR.
---

# implement-ticket

The procedure the agent follows for one Jira ticket. The deterministic controller
sequences these as separate `claude -p` calls (plan → implement → review); this
skill is the canonical description so behaviour is consistent.

## Steps

1. **Plan (plan mode, no code).** Read the ticket. Explore narrowly (ripgrep).
   Decide: does this need any architecture decision?
   - Yes → write one ADR per decision (context · options · trade-offs ·
     recommendation). Stop. The controller transitions the ticket to
     **Needs Decision** and assigns the boss. Write no code.
   - No → produce a tight implementation plan.

2. **Implement (after the plan is approved).** Apply the minimal change in the
   ticket's worktree. Add/adjust tests. Stay in scope. Never touch `main`/`.env`.

3. **Build/test** is run by the controller (not you). If it fails, you get the
   failure back and fix it — bounded retries.

4. **Review (fresh session, diff only).** Independent, adversarial check. If a
   structural problem appears, escalate it as an ADR — do not self-decide.

5. **PR.** On a clean review the controller opens a PR on the feature branch and
   assigns the boss. The agent never merges.

## Guardrails
- All external I/O goes through the Secrets Broker; you have no keys and no network.
- Hooks block `.env`, `main`, and direct network calls.
