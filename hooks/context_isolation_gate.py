#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Context Isolation Gate — SubagentStop hook.

Blocks sub-agent returns that leak raw data into the orchestrator's context.
Fires when a sub-agent finishes and is about to return its message to the parent.

Detection heuristics:
  - Message exceeds 200 lines (summaries should be < 100 lines)
  - Contains hex dump patterns (0x addresses, hex bytes)
  - Contains large JSON arrays (>10 elements inline)
  - Contains excessive raw memory addresses

Only activates when:
  - protocol-state.json has "context-isolation" in active_protocols, OR
  - .claude/skills/context-isolation-protocol/SKILL.md exists (skill deployed)

Exit codes:
  0 = allow (pass through)
  2 = block (instructs sub-agent to write to filesystem instead)

Delegation: the primary path routes through the mentu_policy claude adapter
(one decision authority, golden-parity gated). If mentu_policy cannot be
imported or the pipeline faults for ANY reason, this file falls back to the
verbatim inline legacy logic below — so this LIVE hook never depends on the
package being present. The outermost guard fails open (exit 0).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def _bootstrap_policy_path() -> None:
    """Make ``mentu_policy`` importable relative to THIS file (repo:
    hooks/../mentu_policy; a future deployed copy: alongside in hooks/)."""
    here = Path(__file__).resolve().parent
    for cand in (here.parent, here):
        if (cand / "mentu_policy" / "__init__.py").exists():
            p = str(cand)
            if p not in sys.path:
                sys.path.insert(0, p)
            return


def is_active() -> bool:
    """Check if context isolation enforcement should be active."""
    # Check protocol state file (set by ralph-seq)
    try:
        state_file = Path(".claude/protocol-state.json")
        if state_file.exists():
            state = json.loads(state_file.read_text())
            if "context-isolation" in state.get("active_protocols", []):
                return True
    except (json.JSONDecodeError, OSError):
        pass

    # Check if skill is deployed (always-on for repos with the skill)
    if Path(".claude/skills/context-isolation-protocol/SKILL.md").exists():
        return True

    return False


def check_message(message: str) -> str | None:
    """Check message for raw data leakage. Returns reason string if blocked, None if OK."""
    lines = message.split("\n")
    line_count = len(lines)

    # Check 1: Excessive line count (summaries should be < 100 lines)
    if line_count > 200:
        return f"Message has {line_count} lines (limit: 200). Write to .claude/summaries/ instead."

    # Check 2: Hex dump patterns — e.g. "0x1a2b3c4d" repeated
    hex_pattern = re.compile(r"0x[0-9a-fA-F]{6,}")
    hex_matches = hex_pattern.findall(message)
    if len(hex_matches) > 15:
        return f"Message contains {len(hex_matches)} hex addresses. Write findings to filesystem."

    # Check 3: Raw hex byte sequences (e.g. "ff e0 3c 7a" patterns)
    hex_bytes_pattern = re.compile(r"(?:[0-9a-fA-F]{2}\s){8,}")
    if len(hex_bytes_pattern.findall(message)) > 5:
        return "Message contains raw hex byte dumps. Summarize or write to filesystem."

    # Check 4: Large inline JSON arrays (>10 elements)
    json_array_pattern = re.compile(r"\[(?:[^[\]]*,){10,}[^[\]]*\]")
    if json_array_pattern.search(message):
        return "Message contains large JSON arrays. Write structured data to filesystem."

    return None


def _legacy_check(stdin_text: str) -> None:
    """Verbatim inline fallback (the original hook logic) — used only when the
    mentu_policy pipeline is unavailable. Same external contract."""
    try:
        hook_input = json.loads(stdin_text)
    except (json.JSONDecodeError, EOFError, ValueError):
        sys.exit(0)

    if not is_active():
        sys.exit(0)

    message = hook_input.get("last_assistant_message", "")
    if not message:
        sys.exit(0)

    reason = check_message(message)
    if reason:
        print(
            f"BLOCKED by context-isolation-gate: {reason}\n\n"
            "The sub-agent must write its findings to a file under .claude/summaries/ "
            "and return ONLY the file path. Raw data must not leak into the orchestrator's "
            "context window. See .claude/skills/context-isolation-protocol/SKILL.md for details."
        )
        sys.exit(2)

    sys.exit(0)


def main():
    stdin_text = sys.stdin.read()

    # Primary path: the mentu_policy claude adapter pipeline.
    try:
        _bootstrap_policy_path()
        from mentu_policy.adapters import shim
        stdout, code = shim.run("claude", "subagent_stop", stdin_text)
        if stdout:
            sys.stdout.write(stdout)
        sys.exit(code)
    except SystemExit:
        raise
    except BaseException:
        pass  # any import/runtime fault -> verbatim inline fallback

    _legacy_check(stdin_text)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        sys.stderr.write(f"context_isolation_gate error: {e}\n")
        sys.exit(0)  # Fail open — don't block on hook errors
