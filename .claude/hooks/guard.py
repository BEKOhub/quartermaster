#!/usr/bin/env python3
"""PreToolUse guard hook for the autonomous agent.

Reads the hook event JSON on stdin and BLOCKS (exit 2) dangerous actions:
  - editing/writing `.env`, secrets, or vault paths
  - pushing to / merging `main`, force-pushing
  - reaching the network directly (curl/wget) — only the broker may do external I/O

Wire it up in settings.json under hooks.PreToolUse. Exit 0 = allow, exit 2 = block.
"""
import json
import re
import sys

BLOCK_PATH = re.compile(r"(^|/)\.env|/secrets?/|/vault/|id_rsa|\.pem$", re.I)
BLOCK_BASH = [
    re.compile(r"\bgit\s+push\b.*\borigin\s+main\b", re.I),
    re.compile(r"\bgit\s+push\b.*--force", re.I),
    re.compile(r"\bgit\s+(merge|checkout)\s+main\b", re.I),
    re.compile(r"\b(curl|wget|nc|ncat)\b", re.I),
    re.compile(r"\brm\s+-rf\s+/", re.I),
]


def block(reason: str) -> None:
    print(f"BLOCKED by guard hook: {reason}", file=sys.stderr)
    sys.exit(2)


def main() -> None:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)  # fail open on malformed input; nothing to inspect

    tool = event.get("tool_name", "")
    inp = event.get("tool_input", {}) or {}

    if tool in ("Edit", "Write", "NotebookEdit"):
        path = inp.get("file_path", "") or inp.get("path", "")
        if BLOCK_PATH.search(path):
            block(f"writing protected path: {path}")

    if tool == "Bash":
        cmd = inp.get("command", "")
        for pat in BLOCK_BASH:
            if pat.search(cmd):
                block(f"forbidden command: {cmd}")

    sys.exit(0)


if __name__ == "__main__":
    main()
