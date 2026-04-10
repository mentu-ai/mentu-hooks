#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Mentu PostToolUse Hook - Auto-captures file modifications as evidence.

This hook runs after Edit or Write tools are used and
captures the file modification as a Mentu memory for later use as evidence.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from mentu_local_client import MentuLocalClient

EVIDENCE_LOG = Path(".claude/mentu_evidence.json")


def capture_evidence(body: str) -> str | None:
    """Capture a memory as evidence, return ID."""
    return MentuLocalClient.capture(body, kind="evidence")


def append_to_evidence_log(mem_id: str, file_path: str, evidence_type: str) -> None:
    """Store evidence for later use."""
    log = []
    if EVIDENCE_LOG.exists():
        try:
            log = json.loads(EVIDENCE_LOG.read_text())
        except Exception:
            log = []

    log.append({
        "id": mem_id,
        "file": file_path,
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "type": evidence_type
    })

    EVIDENCE_LOG.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE_LOG.write_text(json.dumps(log, indent=2))


def main():
    """Main hook entry point."""
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        print(json.dumps({}))
        sys.exit(0)

    # Guard: daemon must be running
    if not MentuLocalClient.is_available():
        print(json.dumps({}))
        sys.exit(0)

    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})

    # Capture file changes AND significant bash outputs
    if tool_name not in ("Edit", "Write", "Bash"):
        print(json.dumps({}))
        sys.exit(0)

    file_path = tool_input.get("file_path") or tool_input.get("path", "")
    tool_output = input_data.get("tool_output", "")

    # Bash: only capture test/build results (not every ls/cd/echo)
    if tool_name == "Bash":
        command = tool_input.get("command", "")
        is_build = any(kw in command for kw in ["build", "compile", "tsc", "swift build", "cargo build", "npm run build"])
        is_test = any(kw in command for kw in ["test", "swift test", "cargo test", "npm test", "pytest", "jest"])
        if not is_build and not is_test:
            print(json.dumps({}))
            sys.exit(0)

        # Determine result
        exit_code = input_data.get("tool_exit_code", 0)
        if is_test:
            evidence_type = "test_pass" if exit_code == 0 else "test_fail"
            body = f"Test {'passed' if exit_code == 0 else 'failed'}: {command[:100]}"
        else:
            evidence_type = "build_pass" if exit_code == 0 else "build_fail"
            body = f"Build {'passed' if exit_code == 0 else 'failed'}: {command[:100]}"

        # Include last 200 chars of output for context
        if tool_output:
            body += f"\n{str(tool_output)[-200:]}"

    elif not file_path:
        print(json.dumps({}))
        sys.exit(0)

    # Build evidence body and type for file operations
    elif tool_name == "Write":
        body = f"Created: {file_path}"
        evidence_type = "file_created"
    else:
        body = f"Modified: {file_path}"
        evidence_type = "file_modified"

    # Capture as evidence memory
    mem_id = capture_evidence(body)

    if mem_id:
        append_to_evidence_log(mem_id, file_path, evidence_type)
        sys.stderr.write(f"[Mentu] Evidence captured: {mem_id} ({evidence_type})\n")

        # Push to cloud (best-effort)
        MentuLocalClient.sync()

        # Annotate active commitment with evidence (best-effort)
        try:
            active_cmt_file = Path(".mentu/active_commitment")
            if active_cmt_file.exists():
                cmt_id = active_cmt_file.read_text().strip()
                if cmt_id.startswith("cmt_"):
                    MentuLocalClient.annotate(cmt_id, f"Evidence: {mem_id} — {evidence_type}: {file_path}")
        except Exception:
            pass

    print(json.dumps({}))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        sys.stderr.write(f"PostToolUse hook error: {e}\n")
        print(json.dumps({}))
        sys.exit(0)
