#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Mentu SessionStart Hook - Injects claimed commitments into Claude's context.

This hook runs when a Claude Code session starts and returns context
about any commitments claimed by this agent.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Dict

sys.path.insert(0, str(Path(__file__).parent))
from mentu_local_client import MentuLocalClient
from genesis_reader import GenesisReader


def resolve_actor() -> str:
    """Resolve actor identity in type:name format.

    Priority:
    1. MENTU_ACTOR env var (if already type:name)
    2. Claude Code process detection → agent:claude-{workspace}
    3. OS username → human:{username}
    """
    existing = os.environ.get("MENTU_ACTOR", "")
    if ":" in existing and existing not in ("", "user"):
        return existing

    # Detect Claude Code by its env markers
    if os.environ.get("CLAUDE_CODE") or os.environ.get("CLAUDE_ENV_FILE"):
        ws_name = Path.cwd().name
        return f"agent:claude-{ws_name}"

    # Fallback: human identity from OS
    username = os.environ.get("USER", "unknown")
    return f"human:{username}"


def get_active_commitment() -> str:
    """Get active commitment from tracking file."""
    try:
        active_file = Path(".mentu/active_commitment")
        if active_file.exists():
            cmt_id = active_file.read_text().strip()
            if cmt_id.startswith("cmt_"):
                return cmt_id
    except Exception:
        pass
    return ""


def close_stale_commitment(cmt_id: str) -> None:
    """Submit + close a leftover commitment from a crashed session."""
    # Submit (best-effort)
    MentuLocalClient.submit(cmt_id)

    # Close (best-effort)
    MentuLocalClient.close(cmt_id)

    # Clear tracking file
    Path(".mentu/active_commitment").unlink(missing_ok=True)


def ensure_active_commitment() -> str:
    """Create a commitment for this session if none exists. Returns cmt_id or ''."""
    active_file = Path(".mentu/active_commitment")
    actor = os.environ.get("MENTU_ACTOR", "agent:claude-subtrace")

    # Already have one
    if active_file.exists():
        existing = active_file.read_text().strip()
        if existing.startswith("cmt_"):
            return existing

    # Step 1: Capture a session-start memory
    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5
        ).stdout.strip() or "unknown"
    except Exception:
        branch = "unknown"

    session_body = f"Session started on branch {branch}"
    mem_id = MentuLocalClient.capture(session_body, kind="session")
    if not mem_id:
        return ""

    # Step 2: Commit from that memory
    cmt_id = MentuLocalClient.commit(f"Work session: {branch}", actor=actor, source=mem_id)
    if not cmt_id:
        return ""

    # Step 3: Claim it
    MentuLocalClient.claim(cmt_id)

    # Step 4: Write tracking file
    active_file.parent.mkdir(parents=True, exist_ok=True)
    active_file.write_text(cmt_id)

    return cmt_id


def get_lifecycle_summary() -> str:
    """Get commitment lifecycle summary from mentu sync status."""
    result = MentuLocalClient.sync()
    if result:
        return json.dumps(result)
    return ""


def get_claimed_commitments() -> List[Dict]:
    """Get commitments claimed by this agent."""
    actor = os.environ.get("MENTU_ACTOR", "agent:claude-subtrace")
    all_commitments = MentuLocalClient.list_commitments(state="claimed")
    return [c for c in all_commitments if c.get("owner") == actor]


def main():
    """Main hook entry point - returns context to inject."""
    # Read hook input from stdin to determine session source
    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        hook_input = {}

    # Resolve and export actor identity before anything else
    actor = resolve_actor()
    os.environ["MENTU_ACTOR"] = actor

    # Guard: daemon must be running
    if not MentuLocalClient.is_available():
        sys.exit(0)

    source = hook_input.get("source", "startup")

    # Sync with cloud before checking commitments (best-effort)
    MentuLocalClient.sync()

    # Only manage commitment lifecycle on new sessions or clear
    # Resume and compact are the same session — don't touch the active commitment
    if source in ("startup", "clear"):
        stale = get_active_commitment()
        if stale:
            close_stale_commitment(stale)
        active_cmt = ensure_active_commitment()
    else:
        active_cmt = get_active_commitment()

    claimed = get_claimed_commitments()
    lifecycle_json = get_lifecycle_summary()

    has_context = claimed or active_cmt or lifecycle_json

    if not has_context:
        # No commitments = no context to inject
        sys.exit(0)

    # Read Genesis Key for governance context
    genesis = GenesisReader()
    step_label = os.environ.get("MENTU_STEP_LABEL", "")
    step_tier = os.environ.get("MENTU_STEP_TIER", "")
    effective_tier = genesis.get_step_tier(int(step_tier) if step_tier.isdigit() else None)

    # Count accumulated evidence
    evidence_count = 0
    evidence_log = Path(".claude/mentu_evidence.json")
    if evidence_log.exists():
        try:
            evidence_count = len(json.loads(evidence_log.read_text()))
        except Exception:
            pass

    # Format for injection
    context = "## Mentu Lifecycle State\n\n"

    # Active commitment (from tracking file)
    if active_cmt:
        context += f"**Active commitment (in-progress):** `{active_cmt}`\n"
        if step_label:
            context += f"**Step:** `{step_label}`\n"
        context += f"**Validation tier:** {effective_tier}"
        tier_desc = {1: "(technical only)", 2: "(technical + safety)", 3: "(technical + safety + intent)"}
        context += f" {tier_desc.get(effective_tier, '')}\n"
        if evidence_count:
            context += f"**Evidence captured so far:** {evidence_count} items\n"
        context += "\n"

    # Genesis Key governance
    context += genesis.format_context(active_cmt) + "\n"

    # Role-based permissions from genesis
    if genesis.exists:
        role = genesis.resolve_role(actor)
        allowed = genesis.get_allowed_ops(actor)
        denied = genesis.get_denied_ops(actor)
        allowed_str = ", ".join(allowed) if allowed else "none"
        denied_str = ", ".join(denied) if denied else "none"
        context += f"Your role: {role}. You can: {allowed_str}. You CANNOT: {denied_str}.\n"
    context += "\n"

    # Claimed commitments
    if claimed:
        context += "**Claimed commitments** — you MUST submit each before stopping:\n\n"
        for cmt in claimed:
            context += f"- `{cmt['id']}`: {cmt['body']}\n"
        context += "\n"

    # Lifecycle summary
    if lifecycle_json:
        try:
            status = json.loads(lifecycle_json)
            counts = []
            for state in ["open", "claimed", "in_review", "closed"]:
                count = status.get(state, 0)
                if count:
                    counts.append(f"{state}: {count}")
            if counts:
                context += f"**Lifecycle counts:** {', '.join(counts)}\n\n"
        except (json.JSONDecodeError, TypeError):
            pass

    context += "Use `mentu submit <id> --summary '...'` when complete."

    # Memory pointer — count learning signals, don't inject content
    ledger_path = Path(".mentu/ledger.jsonl")
    if ledger_path.exists():
        try:
            learning_count = sum(
                1 for line in ledger_path.read_text().splitlines()
                if '"kind":"learning"' in line or '"kind":"finding"' in line
            )
            if learning_count > 0:
                context += f"\n\n**Memories:** {learning_count} learning signals in `.mentu/ledger.jsonl`"
        except Exception:
            pass

    # Actor identity from Genesis Key (2 lines, not a persona dump)
    actor = os.environ.get("MENTU_ACTOR", "agent:claude-subtrace")
    if genesis.exists:
        context += f"\n**Actor:** `{actor}` | **Tier:** {effective_tier}"

    print(context)

    # Export env vars for agent + downstream hooks
    env_file = os.environ.get("CLAUDE_ENV_FILE", "")
    session_id = hook_input.get("session_id", "")
    if env_file:
        exports = []
        if session_id:
            exports.append(f'export MENTU_SESSION_ID="{session_id}"')
        exports.append(f'export MENTU_STEP_TIER="{effective_tier}"')
        exports.append(f'export MENTU_ACTOR="{actor}"')
        # Workspace name from genesis key or directory
        ws_name = genesis.config.get("identity", {}).get("name", Path.cwd().name) if genesis.exists else Path.cwd().name
        exports.append(f'export MENTU_WORKSPACE="{ws_name}"')
        with open(env_file, "a") as f:
            f.write("\n".join(exports) + "\n")

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        sys.stderr.write(f"SessionStart hook error: {e}\n")
        sys.exit(0)
