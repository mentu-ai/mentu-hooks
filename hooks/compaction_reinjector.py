#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Compaction Reinjector — SessionStart hook (matcher: compact).

After context compaction, reads .claude/pre-compact-state.json and outputs
a context block to stdout that gets injected into Claude's context.
Reminds the agent which protocols are active and what state was preserved.

Only fires on SessionStart with source=compact.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main():
    # Read hook input to check source
    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        hook_input = {}

    # Only activate on compact source
    if hook_input.get("source") != "compact":
        sys.exit(0)

    state_file = Path(".claude/pre-compact-state.json")
    if not state_file.exists():
        sys.exit(0)

    try:
        state = json.loads(state_file.read_text())
    except (json.JSONDecodeError, OSError):
        sys.exit(0)

    protocols = state.get("active_protocols", [])
    if not protocols:
        sys.exit(0)

    # Build context injection block
    context = "## Protocol State (restored after compaction)\n\n"
    context += f"**Active protocols:** {', '.join(protocols)}\n"

    step_label = state.get("step_label", "")
    if step_label:
        context += f"**Current step:** {step_label}\n"

    seq_name = state.get("sequence_name", "")
    if seq_name:
        context += f"**Sequence:** {seq_name}\n"

    context += "\n"

    # Add reminders
    reminders = state.get("reminders", [])
    if reminders:
        context += "**IMPORTANT reminders:**\n"
        for r in reminders:
            context += f"- {r}\n"
        context += "\n"

    # Add recon artifact info from any active gate protocol
    for key, val in state.items():
        if key.endswith("_gate") and isinstance(val, dict):
            recon = val.get("recon_artifact", "")
            if recon:
                context += f"**Recon artifact ({key}):** `{recon}`"
                if Path(recon).exists():
                    context += " (exists on disk)"
                else:
                    context += " (MISSING)"
                context += "\n"

    if state.get("auto_review"):
        context += "\n**Auto-review is ON** — your exit will be gated on: build pass, LOOP_COMPLETE marker, and git changes.\n"

    print(context)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        sys.stderr.write(f"compaction_reinjector error: {e}\n")
        sys.exit(0)
