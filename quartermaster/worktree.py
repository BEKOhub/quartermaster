"""Git worktree manager: one isolated checkout + branch per ticket, so parallel
tickets never collide and the agent can't touch main.

In MOCK_MODE (or when REPO_PATH isn't a git repo) this becomes a no-op that just
reports the branch name, so the loop still runs.
"""
from __future__ import annotations

import os
import shutil
import subprocess

from .config import Settings
from .logging_setup import get_logger

log = get_logger("worktree")


class Worktree:
    def __init__(self, path: str, branch: str, active: bool) -> None:
        self.path = path
        self.branch = branch
        self.active = active  # False = mock / no real git


class WorktreeManager:
    def __init__(self, settings: Settings) -> None:
        self.s = settings

    def _is_git_repo(self) -> bool:
        return os.path.isdir(os.path.join(self.s.repo_path, ".git"))

    def branch_for(self, ticket_key: str, slug: str) -> str:
        return f"{self.s.github_branch_prefix}{ticket_key}-{slug}"

    def create(self, ticket_key: str, slug: str) -> Worktree:
        branch = self.branch_for(ticket_key, slug)
        if self.s.mock_mode or not self._is_git_repo():
            log.info("[no-op worktree] %s on branch %s", ticket_key, branch)
            return Worktree(path=self.s.repo_path, branch=branch, active=False)

        os.makedirs(self.s.worktrees_path, exist_ok=True)
        wt_path = os.path.join(self.s.worktrees_path, ticket_key)
        if os.path.exists(wt_path):
            log.info("worktree %s already exists — reusing", wt_path)
        else:
            try:
                self._git(["worktree", "add", "-b", branch, wt_path,
                           self.s.github_base_branch])
            except RuntimeError:
                # Branch may already exist (previous failed run); try without -b.
                if os.path.exists(wt_path):
                    shutil.rmtree(wt_path, ignore_errors=True)
                self._git(["worktree", "add", wt_path, branch])
        # Verify the worktree was created and is on the right branch.
        actual = self._git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=wt_path, capture=True).strip()
        if actual != branch:
            log.warning("worktree branch mismatch: expected %s got %s", branch, actual)
        return Worktree(path=wt_path, branch=branch, active=True)

    def commit_all(self, wt: Worktree, message: str) -> bool:
        """Stage + commit everything in the worktree. Returns True if a commit
        was made (False if there was nothing to commit)."""
        if not wt.active:
            log.info("[no-op worktree] would commit: %s", message)
            return True
        self._git(["add", "-A"], cwd=wt.path)
        status = self._git(["status", "--porcelain"], cwd=wt.path, capture=True)
        if not status.strip():
            return False
        self._git(["commit", "-m", message], cwd=wt.path)
        return True

    def push(self, wt: Worktree) -> None:
        if not wt.active:
            log.info("[no-op worktree] would push %s", wt.branch)
            return
        # Verify origin exists before pushing.
        remotes = self._git(["remote"], capture=True).strip()
        if "origin" not in remotes.splitlines():
            raise RuntimeError("no 'origin' remote configured — cannot push")
        self._git(["push", "-u", "origin", wt.branch], cwd=wt.path)

    def remove(self, wt: Worktree) -> None:
        """Remove the worktree and its branch. Safe to call even if already gone."""
        if not wt.active:
            return
        try:
            self._git(["worktree", "remove", "--force", wt.path])
        except RuntimeError as e:
            log.warning("worktree remove failed (may already be gone): %s", e)
            # Fall back to direct directory removal so orphans don't accumulate.
            if os.path.exists(wt.path):
                shutil.rmtree(wt.path, ignore_errors=True)
                log.info("forcibly removed worktree directory %s", wt.path)

    def _git(self, args: list[str], cwd: str | None = None, capture: bool = False) -> str:
        cwd = cwd or self.s.repo_path
        proc = subprocess.run(["git", *args], cwd=cwd, text=True,
                              capture_output=True)
        if proc.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
        return proc.stdout if capture else ""
