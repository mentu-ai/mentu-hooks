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
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


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


def main():
    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
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


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        sys.stderr.write(f"context_isolation_gate error: {e}\n")
        sys.exit(0)  # Fail open — don't block on hook errors
