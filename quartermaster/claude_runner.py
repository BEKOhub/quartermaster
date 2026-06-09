"""Drives the Claude Code CLI as disciplined automation, and parses its JSON.

Best practices baked in (per JIRA_AGENT_DESIGN.md §4):
  - always `claude -p --output-format json` and parse the JSON; never scrape text
  - `--json-schema` forces a machine-readable verdict into structured_output
  - `--session-id` / `--resume` for deterministic per-ticket sessions
  - `--model` per stage; `--allowedTools` to avoid permission prompts
  - run inside the ticket's git worktree (cwd) so edits can't touch main
  - the CLI gets NO external network — only the broker has keys

MOCK_MODE returns canned structured output so the whole loop runs with no token
spend and no credentials.
"""
from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Optional

from .config import Settings
from .logging_setup import get_logger
from .models import ClaudeResult

log = get_logger("claude")

# Tight default tool allowlist for unattended runs.
DEFAULT_ALLOWED_TOOLS = "Read Edit Write Bash(git*) Bash(rg*) Bash(ls*) Bash(cat*) Grep Glob"


@dataclass
class RunSpec:
    prompt: str
    session_id: str
    stage: str                 # plan | implement | review
    model: str
    cwd: str
    resume: bool = False
    json_schema_path: Optional[str] = None
    append_system_prompt: Optional[str] = None
    allowed_tools: str = DEFAULT_ALLOWED_TOOLS
    permission_mode: str = "plan"  # plan mode for the planner; default for others
    meta: dict = None  # ticket hints for the mock brain; ignored by the real CLI

    def __post_init__(self) -> None:
        if self.meta is None:
            self.meta = {}


class ClaudeRunner:
    def __init__(self, settings: Settings, mock_provider=None) -> None:
        self.s = settings
        # mock_provider: callable(RunSpec) -> dict (structured), used in MOCK_MODE
        self._mock = mock_provider

    def run(self, spec: RunSpec) -> ClaudeResult:
        if self.s.mock_mode:
            return self._run_mock(spec)
        return self._run_real(spec)

    # --- real CLI ---------------------------------------------------------
    def _build_argv(self, spec: RunSpec) -> list[str]:
        argv = [self.s.claude_bin, "-p", spec.prompt, "--output-format", "json",
                "--model", spec.model, "--allowedTools", spec.allowed_tools]
        if spec.resume:
            argv += ["--resume", spec.session_id]
        else:
            argv += ["--session-id", spec.session_id]
        if spec.json_schema_path:
            argv += ["--json-schema", spec.json_schema_path]
        if spec.append_system_prompt:
            argv += ["--append-system-prompt", spec.append_system_prompt]
        if spec.permission_mode:
            argv += ["--permission-mode", spec.permission_mode]
        return argv

    def _run_real(self, spec: RunSpec) -> ClaudeResult:
        argv = self._build_argv(spec)
        log.info("claude %s stage=%s model=%s session=%s",
                 "resume" if spec.resume else "new", spec.stage, spec.model, spec.session_id)
        start = time.monotonic()
        try:
            proc = subprocess.run(
                argv, cwd=spec.cwd, capture_output=True, text=True, timeout=1800,
            )
        except subprocess.TimeoutExpired:
            return ClaudeResult(session_id=spec.session_id, cost_usd=0.0,
                                error="claude CLI timed out")
        dur_ms = int((time.monotonic() - start) * 1000)
        if proc.returncode != 0:
            return ClaudeResult(session_id=spec.session_id, cost_usd=0.0,
                                error=f"claude exited {proc.returncode}: {proc.stderr[:500]}",
                                duration_ms=dur_ms)
        res = self._parse(proc.stdout, spec.session_id)
        res.duration_ms = dur_ms
        return res

    @staticmethod
    def _parse(stdout: str, fallback_session: str) -> ClaudeResult:
        try:
            data: dict[str, Any] = json.loads(stdout)
        except json.JSONDecodeError as e:
            return ClaudeResult(session_id=fallback_session, cost_usd=0.0,
                                error=f"unparseable CLI output: {e}")
        structured = data.get("structured_output") or {}
        # CLI sometimes nests structured output inside the result string; tolerate both.
        if not structured and isinstance(data.get("result"), str):
            try:
                maybe = json.loads(data["result"])
                if isinstance(maybe, dict):
                    structured = maybe
            except (json.JSONDecodeError, TypeError):
                pass
        usage = data.get("usage") or {}
        return ClaudeResult(
            session_id=data.get("session_id", fallback_session),
            cost_usd=float(data.get("total_cost_usd", 0.0) or 0.0),
            structured=structured,
            text=data.get("result", "") if isinstance(data.get("result"), str) else "",
            error=data.get("error"),
            input_tokens=int(usage.get("input_tokens", 0) or 0),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
            cache_read_tokens=int(usage.get("cache_read_input_tokens", 0) or 0),
            cache_write_tokens=int(usage.get("cache_creation_input_tokens", 0) or 0),
        )

    # --- mock -------------------------------------------------------------
    def _run_mock(self, spec: RunSpec) -> ClaudeResult:
        if self._mock is None:
            raise RuntimeError("MOCK_MODE is on but no mock provider was supplied")
        start = time.monotonic()
        if self.s.mock_step_delay_seconds > 0:
            time.sleep(self.s.mock_step_delay_seconds)
        structured = self._mock(spec)
        # Pretend small, stage-dependent costs/tokens so analytics have real data.
        cost = {"plan": 0.02, "implement": 0.35, "review": 0.08}.get(spec.stage, 0.05)
        intok = {"plan": 1800, "implement": 5200, "review": 2400}.get(spec.stage, 1500)
        outtok = {"plan": 400, "implement": 1600, "review": 500}.get(spec.stage, 300)
        # Resumed sessions model prompt-cache reuse: most input served from cache.
        cache = int(intok * 0.85) if spec.resume else int(intok * 0.25)
        intok -= cache
        log.info("[mock claude] stage=%s session=%s -> %s",
                 spec.stage, spec.session_id, structured.get("status"))
        return ClaudeResult(session_id=spec.session_id, cost_usd=cost,
                            structured=structured, text=structured.get("summary", ""),
                            input_tokens=intok, output_tokens=outtok,
                            cache_read_tokens=cache,
                            duration_ms=int((time.monotonic() - start) * 1000))
