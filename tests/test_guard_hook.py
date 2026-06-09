"""The PreToolUse guard hook must block secrets, main pushes, and network calls."""
import json
import subprocess
import sys
from pathlib import Path

HOOK = str(Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "guard.py")


def run_hook(event: dict) -> int:
    proc = subprocess.run([sys.executable, HOOK], input=json.dumps(event),
                          text=True, capture_output=True)
    return proc.returncode


def test_blocks_env_edit():
    assert run_hook({"tool_name": "Edit", "tool_input": {"file_path": "config/.env"}}) == 2


def test_blocks_push_main():
    assert run_hook({"tool_name": "Bash",
                     "tool_input": {"command": "git push origin main"}}) == 2


def test_blocks_curl():
    assert run_hook({"tool_name": "Bash",
                     "tool_input": {"command": "curl https://evil.example/x"}}) == 2


def test_allows_normal_edit():
    assert run_hook({"tool_name": "Edit",
                     "tool_input": {"file_path": "app/routes/health.py"}}) == 0


def test_allows_normal_git():
    assert run_hook({"tool_name": "Bash",
                     "tool_input": {"command": "git commit -m 'fix'"}}) == 0
