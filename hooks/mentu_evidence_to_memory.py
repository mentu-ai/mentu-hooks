#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Mentu Evidence-to-Memory Bridge Hook

Listens for Mentu capture operations (via the evidence log written by
mentu_post_tool.py) and appends evidence entries to .mentu/agent/memories.md.
This bridges the Mentu evidence chain into the Ralph memory injection system.

Trigger: PostToolUse (runs after Edit/Write, same as mentu_post_tool.py)
Flow: File edit -> mentu_post_tool.py captures evidence -> this hook appends to memories
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

EVIDENCE_LOG = Path(".claude/mentu_evidence.json")
MEMORY_FILE = Path(".mentu/agent/memories.md")
BRIDGE_STATE = Path(".mentu/.evidence_bridge_cursor")


def get_new_evidence_entries() -> list[dict]:
    """Read evidence entries that haven't been bridged yet."""
    if not EVIDENCE_LOG.exists():
        return []

    try:
        all_entries = json.loads(EVIDENCE_LOG.read_text())
    except (json.JSONDecodeError, OSError):
        return []

    # Track which entries we've already bridged
    cursor = 0
    if BRIDGE_STATE.exists():
        try:
            cursor = int(BRIDGE_STATE.read_text().strip())
        except (ValueError, OSError):
            cursor = 0

    new_entries = all_entries[cursor:]

    # Update cursor
    if new_entries:
        BRIDGE_STATE.parent.mkdir(parents=True, exist_ok=True)
        BRIDGE_STATE.write_text(str(len(all_entries)))

    return new_entries


def append_to_memories(entries: list[dict]) -> None:
    """Append evidence entries to the memories file."""
    if not entries:
        return

    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)

    if not MEMORY_FILE.exists():
        MEMORY_FILE.write_text("# Ralph Memory Context\n\n## Evidence Trail\n\n")

    content = MEMORY_FILE.read_text()

    # Build new evidence lines
    lines = []
    for entry in entries:
        mem_id = entry.get("id", "unknown")
        file_path = entry.get("file", "unknown")
        evidence_type = entry.get("type", "unknown")
        timestamp = entry.get("ts", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        lines.append(f"- `{mem_id}` | {evidence_type} | `{file_path}` | {timestamp}")

    evidence_block = "\n".join(lines) + "\n"

    # Append under Evidence Trail section
    marker = "## Evidence Trail"
    if marker in content:
        # Insert after the marker (and any existing content)
        content = content.rstrip() + "\n" + evidence_block
    else:
        content += f"\n{marker}\n\n{evidence_block}"

    MEMORY_FILE.write_text(content)


def main():
    """Main hook entry point."""
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        print(json.dumps({}))
        sys.exit(0)

    tool_name = input_data.get("tool_name", "")

    # Only run after file modification tools (same trigger as mentu_post_tool.py)
    if tool_name not in ("Edit", "Write"):
        print(json.dumps({}))
        sys.exit(0)

    # Bridge new evidence entries to memories
    new_entries = get_new_evidence_entries()
    if new_entries:
        append_to_memories(new_entries)
        sys.stderr.write(
            f"[Ralph Memory] Bridged {len(new_entries)} evidence entries to memories.md\n"
        )

    print(json.dumps({}))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        sys.stderr.write(f"Evidence-to-memory bridge error: {e}\n")
        print(json.dumps({}))
        sys.exit(0)
