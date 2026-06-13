#!/usr/bin/env python3
"""mentu_policy.probes — the injectable side-effect seam used by gates (M2a).

Gates are pure functions over the ABI; every repo/world observation they
need (build run, git diff, ledger tail, wall clock) comes through this
seam. Production uses the real subprocess implementation below with the
same timeouts as the legacy hook scripts; tests inject duck-typed fakes.

Probes may raise (subprocess.TimeoutExpired, OSError, ...): the verbatim
rule bodies in gates.py keep the legacy try/except structure, and every
gate is additionally wrapped fail-open, so a probe fault degrades to a
missed check — never a wrongful refusal.
"""
from __future__ import annotations

import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple


class Probes:
    """Real side-effect implementations. `cwd` scopes the git/build calls
    to the workspace under judgment (the legacy scripts relied on process
    CWD; the pure core cannot)."""

    def __init__(self, cwd: Optional[str] = None):
        self.cwd = cwd or None

    def run_build(self, cmd: str) -> Tuple[int, str, str]:
        """Run the build command. shlex.split + shell=False so a
        repo-controlled CLAUDE.md build line can never reach a shell
        (BUILD doc M2a security property). Timeout matches the legacy
        Dual Triad technical validator (120s)."""
        args = shlex.split(cmd)
        result = subprocess.run(
            args, shell=False, capture_output=True, text=True,
            timeout=120, cwd=self.cwd,
        )
        return (result.returncode, result.stdout, result.stderr)

    def git_diff_text(self) -> str:
        """`git diff HEAD` full text (legacy timeout: 30s)."""
        result = subprocess.run(
            ["git", "diff", "HEAD"],
            capture_output=True, text=True, timeout=30, cwd=self.cwd,
        )
        return result.stdout

    def git_changed_files(self) -> List[str]:
        """`git diff --name-only HEAD` (legacy timeout: 10s)."""
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            capture_output=True, text=True, timeout=10, cwd=self.cwd,
        )
        return result.stdout.strip().splitlines()

    def git_status_dirty(self) -> bool:
        """`git status --porcelain` non-empty (legacy timeout: 10s)."""
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=10, cwd=self.cwd,
        )
        return bool(result.stdout.strip())

    def read_ledger_lines(self, path: str) -> Optional[List[str]]:
        """Ledger lines, or None when the ledger file is absent/unreadable.
        None-vs-[] matters: the legacy review gate keys its verdict write
        on the ledger file EXISTING, even when empty."""
        try:
            ledger = Path(path)
            if not ledger.exists():
                return None
            return ledger.read_text().splitlines()
        except OSError:
            return None

    def now(self) -> datetime:
        return datetime.now(timezone.utc)
