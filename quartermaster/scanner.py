"""Output filtering — scan a proposed diff before it ever becomes a PR.

A 2026 defence-in-depth layer: even though Claude has no keys and no network, a
prompt-injected ticket could try to get it to *write* an exfiltration call,
hardcode a secret, or echo a planted canary token into the code. We scan the diff
and block + escalate on any hit, so a hijacked run cannot ship.

Pure, deterministic, zero-token. Returns a list of findings; empty == clean.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class Severity(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# All patterns compiled once at import time.
_SECRET_PATTERNS = [
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key id", Severity.HIGH),
    (re.compile(r"\b(ASIA|AROA|AIPA|AKIA|ANPA|ANVA|APKA)[A-Z0-9]{16}\b"),
     "AWS key variant", Severity.HIGH),
    (re.compile(r"\bsk-(live|test)_[A-Za-z0-9]{24,}\b"), "Stripe secret key", Severity.HIGH),
    (re.compile(r"\bsk-[A-Za-z0-9]{48,}\b"), "OpenAI-style secret key", Severity.HIGH),
    (re.compile(r"\bghp_[A-Za-z0-9]{36,}\b"), "GitHub personal access token", Severity.HIGH),
    (re.compile(r"\bgho_[A-Za-z0-9]{36,}\b"), "GitHub OAuth token", Severity.HIGH),
    (re.compile(r"\bghs_[A-Za-z0-9]{36,}\b"), "GitHub App installation token", Severity.HIGH),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "Slack token", Severity.HIGH),
    (re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"), "private key", Severity.HIGH),
    # Azure Storage connection strings
    (re.compile(r"DefaultEndpointsProtocol=https?;AccountName=[^;]+;AccountKey=[A-Za-z0-9+/=]{30,}"),
     "Azure Storage connection string", Severity.HIGH),
    # JWT tokens (eyJ... base64 header)
    (re.compile(r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\b"),
     "JWT token", Severity.MEDIUM),
    # Tightly scoped hardcoded credential — require value ≥16 chars to reduce false positives
    (re.compile(r"(?i)\b(secret_key|api_secret|private_key|auth_token)\s*[:=]\s*['\"][^'\"]{16,}['\"]"),
     "hardcoded credential", Severity.MEDIUM),
    # Generic password assignment — require ≥20 chars to avoid catching test fixtures
    (re.compile(r"(?i)\b(password|passwd)\s*[:=]\s*['\"][^'\"]{20,}['\"]"),
     "hardcoded password", Severity.MEDIUM),
]

# Outbound-network calls introduced in code (exfiltration channel).
_NETWORK_PATTERNS = [
    (re.compile(r"\b(curl|wget)\s+https?://", re.I), "shell network call", Severity.HIGH),
    # Exclude localhost / 127.0.0.1 — those are test fixtures, not exfil
    (re.compile(r"requests\.(get|post|put|patch|delete)\s*\(\s*['\"]https?://(?!localhost|127\.)"),
     "python requests outbound call", Severity.MEDIUM),
    (re.compile(r"\bfetch\s*\(\s*['\"]https?://(?!localhost|127\.)", re.I),
     "JS fetch outbound call", Severity.MEDIUM),
    (re.compile(r"\baxios\.(get|post|put|patch|delete)\s*\(\s*['\"]https?://(?!localhost|127\.)",
                re.I), "axios outbound call", Severity.MEDIUM),
    (re.compile(r"\bsocket\.(connect|create_connection)\b", re.I), "raw socket", Severity.HIGH),
    # Bash TCP redirect — classic exfil channel
    (re.compile(r"/dev/tcp/[^/\s]+/\d+"), "bash TCP redirect", Severity.HIGH),
]


@dataclass
class Finding:
    kind: str           # "secret" | "network" | "canary"
    label: str
    line: str
    severity: Severity = field(default=Severity.MEDIUM)


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
            findings.append(Finding("canary", "planted canary token present in code",
                                    line.strip()[:200], Severity.HIGH))
        for pat, label, severity in _SECRET_PATTERNS:
            if pat.search(line):
                findings.append(Finding("secret", label, line.strip()[:200], severity))
        for pat, label, severity in _NETWORK_PATTERNS:
            if pat.search(line):
                findings.append(Finding("network", label, line.strip()[:200], severity))
    return findings


def format_findings(findings: list[Finding]) -> str:
    return "\n".join(
        f"  • [{f.severity.value}/{f.kind}] {f.label}: {f.line}" for f in findings
    )
