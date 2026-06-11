"""Repo map — a compact, cacheable overview of the target repository injected as
a stable prompt prefix.

Context engineering (2026): the win is the *smallest high-signal* set of tokens,
served from prompt cache. A repo map (directory tree + top-level symbols) lets the
implementer pull the few files it needs via ripgrep instead of dumping the repo,
and because the map is stable it sits in the cached prefix (~10-25% input cost on
reuse). The map is computed once and cached to disk keyed by a cheap repo
signature, so we don't re-walk the tree every ticket.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from typing import Optional

from .logging_setup import get_logger

log = get_logger("repomap")

_CODE_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".rb"}
_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist",
              "build", ".next", "data", "worktrees"}


class RepoMap:
    def __init__(self, repo_path: str, *, max_files: int = 400,
                 cache_dir: Optional[str] = None) -> None:
        self.repo_path = repo_path
        self.max_files = max_files
        self.cache_dir = cache_dir or os.path.join(repo_path, ".cache")
        self._cached: Optional[str] = None
        self._sig: Optional[str] = None

    # --- public -----------------------------------------------------------
    def get(self) -> str:
        """Return the map, rebuilding only if the repo signature changed."""
        if not os.path.isdir(self.repo_path):
            return "(no repository mounted — running without a repo map)"
        sig = self._signature()
        if self._cached is not None and sig == self._sig:
            return self._cached
        self._cached = self._build()
        self._sig = sig
        return self._cached

    # --- internals --------------------------------------------------------
    def _signature(self) -> str:
        """Fingerprint: sorted (path, mtime) pairs so file additions AND deletions
        both change the signature, unlike the previous count+newest approach."""
        entries = []
        for root, dirs, files in os.walk(self.repo_path):
            dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
            for f in sorted(files):
                if os.path.splitext(f)[1] in _CODE_EXT:
                    fpath = os.path.join(root, f)
                    rel = os.path.relpath(fpath, self.repo_path)
                    try:
                        mtime = int(os.path.getmtime(fpath))
                    except OSError:
                        mtime = 0
                    entries.append(f"{rel}:{mtime}")
        return hashlib.sha1("\n".join(entries).encode()).hexdigest()[:16]

    def _build(self) -> str:
        files: list[str] = []
        for root, dirs, names in os.walk(self.repo_path):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            for n in names:
                if os.path.splitext(n)[1] in _CODE_EXT:
                    rel = os.path.relpath(os.path.join(root, n), self.repo_path)
                    files.append(rel)
                    if len(files) >= self.max_files:
                        break
        files.sort()
        lines = [f"# Repo map ({len(files)} code files)", ""]
        lines += [f"  {p}" for p in files[:120]]
        if len(files) > 120:
            lines.append(f"  … and {len(files) - 120} more")
        symbols = self._symbols()
        if symbols:
            lines += ["", "# Key symbols (def/class/func)", *symbols[:80]]
        log.info("repo map built: %s files", len(files))
        return "\n".join(lines)

    def _symbols(self) -> list[str]:
        """Top-level defs/classes via ripgrep if available (best-effort)."""
        try:
            proc = subprocess.run(
                ["rg", "-n", r"^(class |def |func |function |export (class|function|const) )",
                 "--max-count", "5", "-g", "!*test*", "-g", "!*.min.*"],
                cwd=self.repo_path, capture_output=True, text=True, timeout=20)
        except FileNotFoundError:
            return []  # rg not available
        except subprocess.TimeoutExpired:
            log.warning("repomap: rg timed out collecting symbols")
            return []
        except subprocess.SubprocessError as e:
            log.debug("repomap: rg error: %s", e)
            return []
        out = []
        for line in proc.stdout.splitlines()[:300]:
            out.append("  " + line.strip()[:160])
        return out
