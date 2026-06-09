"""Per-service least-privilege policy — the safety choke-point.

Each operation the agent can request is classified ALLOW / PROPOSE / DENY.
PROPOSE means "do not execute; open an approval ticket for the boss". DENY means
the operation never happens autonomously. This encodes the policy table from
SECURE_BROKER_AND_QUEUE.md.
"""
from __future__ import annotations

from enum import Enum


class Decision(str, Enum):
    ALLOW = "allow"        # broker executes
    PROPOSE = "propose"    # broker creates an approval ticket; human executes
    DENY = "deny"          # never


# (service, operation) -> Decision. Unknown operations default to DENY.
POLICY: dict[tuple[str, str], Decision] = {
    # Jira: full read + workflow control on the agent's own project.
    ("jira", "search"): Decision.ALLOW,
    ("jira", "get"): Decision.ALLOW,
    ("jira", "comment"): Decision.ALLOW,
    ("jira", "transition"): Decision.ALLOW,
    ("jira", "assign"): Decision.ALLOW,
    ("jira", "delete_project"): Decision.DENY,
    ("jira", "admin"): Decision.DENY,

    # GitHub: open/update a PR on a feature branch only.
    ("github", "open_pr"): Decision.ALLOW,
    ("github", "update_pr"): Decision.ALLOW,
    ("github", "merge_main"): Decision.DENY,
    ("github", "force_push"): Decision.DENY,
    ("github", "change_settings"): Decision.DENY,

    # Cloudflare: read / propose only.
    ("cloudflare", "read"): Decision.ALLOW,
    ("cloudflare", "edit_dns"): Decision.PROPOSE,
    ("cloudflare", "edit_waf"): Decision.PROPOSE,

    # Stripe: read-only / test-mode. No live writes, ever.
    ("stripe", "read"): Decision.ALLOW,
    ("stripe", "write_test"): Decision.ALLOW,
    ("stripe", "write_live"): Decision.DENY,

    # Azure: read status; deploys are proposed, never autonomous.
    ("azure", "read"): Decision.ALLOW,
    ("azure", "propose_deploy"): Decision.PROPOSE,
    ("azure", "deploy_prod"): Decision.DENY,
}


def decide(service: str, operation: str) -> Decision:
    return POLICY.get((service, operation), Decision.DENY)
