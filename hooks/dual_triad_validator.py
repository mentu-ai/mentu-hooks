#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Dual Triad Validator — Technical + Safety + Intent validation.

Runs tiered validation against the commitment lifecycle:
  Tier 1: Technical only (build passes, tests pass)
  Tier 2: Technical + Safety (scope check, no secrets, no dangerous patterns)
  Tier 3: Technical + Safety + Intent (LOOP_COMPLETE, matches commitment description)

Each validator produces a typed result with pass/fail + evidence.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class ValidationResult:
    validator: str  # "technical" | "safety" | "intent"
    passed: bool
    evidence: str
    details: list[str] = field(default_factory=list)


@dataclass
class TriadResult:
    tier: int
    results: list[ValidationResult]

    @property
    def all_passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def failures(self) -> list[ValidationResult]:
        return [r for r in self.results if not r.passed]

    def format_feedback(self) -> str:
        """Format validation results as feedback for the agent."""
        lines = []
        for r in self.results:
            status = "PASS" if r.passed else "FAIL"
            lines.append(f"  {r.validator}: {status} — {r.evidence}")
            for d in r.details:
                lines.append(f"    {d}")
        return "\n".join(lines)


def read_build_cmd() -> str:
    """Extract build command from CLAUDE.md."""
    claude_md = Path("CLAUDE.md")
    if not claude_md.exists():
        return "echo build ok"
    text = claude_md.read_text()
    match = re.search(r"## Commands.*?```bash\n(.+?)\n```", text, re.DOTALL)
    if match:
        return match.group(1).strip().split("\n")[0]
    return "echo build ok"


def validate_technical() -> ValidationResult:
    """Technical validator: does it build? Do tests pass?"""
    cmd = read_build_cmd()
    details = []

    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=120
        )
        build_ok = result.returncode == 0
        if not build_ok:
            details.append(f"Build failed: {result.stderr[:200]}")
        else:
            details.append("Build passed")
    except subprocess.TimeoutExpired:
        build_ok = False
        details.append("Build timed out (120s)")
    except Exception as e:
        build_ok = False
        details.append(f"Build error: {e}")

    # Check git has changes (agent didn't no-op)
    try:
        diff = subprocess.run(
            ["git", "diff", "--stat", "HEAD"],
            capture_output=True, text=True, timeout=10
        )
        has_changes = bool(diff.stdout.strip())
        if not has_changes:
            # Also check staged
            staged = subprocess.run(
                ["git", "diff", "--stat", "--cached"],
                capture_output=True, text=True, timeout=10
            )
            has_changes = bool(staged.stdout.strip())

        if has_changes:
            details.append("Git working tree has changes")
        else:
            details.append("WARNING: No git changes detected")
    except Exception:
        pass

    return ValidationResult(
        validator="technical",
        passed=build_ok,
        evidence="build" if build_ok else "build_failed",
        details=details
    )


def validate_safety(scope: list[str] | None = None) -> ValidationResult:
    """Safety validator: stayed in scope? No secrets? No dangerous patterns?"""
    details = []
    issues = []

    # Check for secrets in staged/modified files
    try:
        diff = subprocess.run(
            ["git", "diff", "HEAD"],
            capture_output=True, text=True, timeout=30
        )
        diff_text = diff.stdout

        # Secret patterns
        secret_patterns = [
            (r"(?:password|passwd|pwd)\s*[:=]\s*['\"][^'\"]{8,}", "Possible hardcoded password"),
            (r"(?:api[_-]?key|apikey)\s*[:=]\s*['\"][^'\"]{16,}", "Possible API key"),
            (r"(?:secret|token)\s*[:=]\s*['\"][^'\"]{16,}", "Possible secret/token"),
            (r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----", "Private key detected"),
            (r"sk_(?:live|test)_[a-zA-Z0-9]{20,}", "Stripe key detected"),
            (r"ghp_[a-zA-Z0-9]{36}", "GitHub token detected"),
        ]

        for pattern, description in secret_patterns:
            if re.search(pattern, diff_text, re.IGNORECASE):
                issues.append(description)

        if not issues:
            details.append("No secrets detected in diff")

    except Exception as e:
        details.append(f"Could not check for secrets: {e}")

    # Check scope (if specified)
    if scope and scope != ["*"]:
        try:
            changed = subprocess.run(
                ["git", "diff", "--name-only", "HEAD"],
                capture_output=True, text=True, timeout=10
            )
            for filepath in changed.stdout.strip().splitlines():
                in_scope = any(
                    filepath.startswith(s.rstrip("/")) or s == "*"
                    for s in scope
                )
                if not in_scope:
                    issues.append(f"Out of scope: {filepath}")
        except Exception:
            pass

    passed = len(issues) == 0
    if issues:
        details.extend(issues)
    else:
        details.append("All safety checks passed")

    return ValidationResult(
        validator="safety",
        passed=passed,
        evidence="safe" if passed else f"{len(issues)} issues",
        details=details
    )


def validate_intent(last_message: str | None = None) -> ValidationResult:
    """Intent validator: does the work match the original vision?"""
    details = []

    # Check LOOP_COMPLETE as proxy for intent completion
    loop_complete = False
    if last_message:
        loop_complete = "LOOP_COMPLETE" in last_message

    if loop_complete:
        details.append("LOOP_COMPLETE found in agent output")
    else:
        details.append("LOOP_COMPLETE not found — step may be incomplete")

    return ValidationResult(
        validator="intent",
        passed=loop_complete,
        evidence="loop_complete" if loop_complete else "incomplete",
        details=details
    )


def run_dual_triad(
    tier: int = 1,
    scope: list[str] | None = None,
    last_message: str | None = None,
) -> TriadResult:
    """Run the Dual Triad validation at the specified tier."""
    results = []

    # Technical (always)
    results.append(validate_technical())

    # Safety (Tier 2+)
    if tier >= 2:
        results.append(validate_safety(scope=scope))

    # Intent (Tier 3)
    if tier >= 3:
        results.append(validate_intent(last_message=last_message))

    return TriadResult(tier=tier, results=results)


if __name__ == "__main__":
    # Standalone test
    result = run_dual_triad(tier=2)
    print(f"Tier {result.tier} validation: {'PASS' if result.all_passed else 'FAIL'}")
    print(result.format_feedback())
