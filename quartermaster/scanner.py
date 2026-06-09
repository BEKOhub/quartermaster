"""Output filtering — scan a proposed diff before it ever becomes a PR.

A 2026 defence-in-depth layer: even though Claude has no keys and no network, a
prompt-injected ticket could try to get it to *write* an exfiltration call,
hardcode a secret, or echo a planted canary token into the code. We scan the diff
and block + escalate on any hit, so a hijacked run cannot ship.

Pure, deterministic, zero-token. Returns a list of findings; empty == clean.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Secret-shaped strings.
_SECRET_PATTERNS = [
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key id"),
    (re.compile(r"\bsk-(live|test)?[A-Za-z0-9]{20,}\b"), "Stripe/OpenAI-style secret key"),
    (re.compile(r"\bghp_[A-Za-z0-9]{30,}\b"), "GitHub personal access token"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "Slack token"),
    (re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"), "private key"),
    (re.compile(r"(?i)\b(secret|password|passwd|api[_-]?key)\s*[:=]\s*['\"][^'\"]{8,}['\"]"),
     "hardcoded credential"),
]
# Outbound-network calls introduced in code (exfiltration channel).
_NETWORK_PATTERNS = [
    (re.compile(r"\b(curl|wget)\s+https?://", re.I), "shell network call"),
    (re.compile(r"requests\.(get|post|put)\s*\(", re.I), "python requests network call"),
    (re.compile(r"\b(fetch|axios|XMLHttpRequest)\s*\(", re.I), "JS network call"),
    (re.compile(r"\bsocket\.(connect|create_connection)\b", re.I), "raw socket"),
]


@dataclass
class Finding:
    kind: str           # "secret" | "network" | "canary"
    label: str
    line: str


def _added_lines(diff: str) -> list[str]:
    """Only inspect ADDED lines (start with '+', not the '+++' header).
    If the input isn't a unified diff, treat every line as added."""
    looks_like_diff = any(l.startswith(("+++", "---", "@@")) for l in diff.splitlines())
    if not looks_like_diff:
        return diff.splitlines()
    out = []
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            out.append(line[1:])
    return out


def scan_diff(diff: str, *, canary_token: str = "") -> list[Finding]:
    findings: list[Finding] = []
    for line in _added_lines(diff):
        if canary_token and canary_token in line:
            findings.append(Finding("canary", "planted canary token present in code", line.strip()[:160]))
        for pat, label in _SECRET_PATTERNS:
            if pat.search(line):
                findings.append(Finding("secret", label, line.strip()[:160]))
        for pat, label in _NETWORK_PATTERNS:
            if pat.search(line):
                findings.append(Finding("network", label, line.strip()[:160]))
    return findings


def format_findings(findings: list[Finding]) -> str:
    return "\n".join(f"  • [{f.kind}] {f.label}: {f.line}" for f in findings)
