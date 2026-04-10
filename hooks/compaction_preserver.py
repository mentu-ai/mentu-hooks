#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Compaction Preserver — PreCompact hook.

Snapshots protocol state before context window compaction so it can be
re-injected after compaction completes. Reads .claude/protocol-state.json
and writes .claude/pre-compact-state.json with additional context hints.

Exit code 0 always (informational only, never blocks compaction).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main():
    state_file = Path(".claude/protocol-state.json")
    if not state_file.exists():
        sys.exit(0)

    try:
        state = json.loads(state_file.read_text())
    except (json.JSONDecodeError, OSError):
        sys.exit(0)

    if not state.get("active_protocols"):
        sys.exit(0)

    # Build compaction-safe state with reminders
    compact_state = {
        "active_protocols": state.get("active_protocols", []),
        "step_label": state.get("step_label", ""),
        "sequence_name": state.get("sequence_name", ""),
        "auto_review": state.get("auto_review", False),
        "reminders": [],
    }

    # Add protocol-specific reminders
    if "context-isolation" in compact_state["active_protocols"]:
        compact_state["reminders"].append(
            "Context Isolation is ACTIVE: sub-agents must write to .claude/summaries/, "
            "never return raw data in messages."
        )

    # Preserve any substrate-gate protocol state (e.g., recon-gate, tool-gate)
    for proto in compact_state["active_protocols"]:
        if proto.endswith("-gate"):
            gate_key = proto.replace("-", "_")
            gate_state = state.get(gate_key, {})
            if gate_state:
                compact_state[gate_key] = gate_state
                recon = gate_state.get("recon_artifact", "")
                if recon:
                    exists = Path(recon).exists()
                    compact_state["reminders"].append(
                        f"{proto} is ACTIVE: recon artifact at {recon} "
                        f"({'EXISTS' if exists else 'MISSING — recon step needed'})."
                    )

    if compact_state["auto_review"]:
        compact_state["reminders"].append(
            "Auto-review is ENABLED: your exit will be gated on build pass + LOOP_COMPLETE."
        )

    # Write pre-compact state
    Path(".claude/pre-compact-state.json").write_text(
        json.dumps(compact_state, indent=2) + "\n"
    )

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        sys.stderr.write(f"compaction_preserver error: {e}\n")
        sys.exit(0)
