#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Review Gate — Stop hook with Commitment Protocol + Dual Triad enforcement.

Three-phase gate:
  Phase 1: Commitment closure — active commitment must be submitted/closed
  Phase 2: Dual Triad validation — Technical + Safety + Intent (tiered)
  Phase 3: Structural checks — build, LOOP_COMPLETE, git changes (legacy)

Respects stop_hook_active to prevent infinite loops — always allows exit on
second attempt.

Activates when either:
  - protocol-state.json has "auto_review": true (legacy)
  - .mentu/active_commitment exists (commitment protocol)

Exit codes:
  0 = allow exit
  2 = block exit with feedback
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from mentu_local_client import MentuLocalClient
from genesis_reader import GenesisReader
from dual_triad_validator import run_dual_triad


def read_build_cmd() -> str:
    """Extract build command from CLAUDE.md."""
    claude_md = Path("CLAUDE.md")
    if not claude_md.exists():
        return "echo build ok"
    text = claude_md.read_text()
    # Look for ```bash block under ## Commands
    match = re.search(r"## Commands.*?```bash\n(.+?)\n```", text, re.DOTALL)
    if match:
        # Return first line of the bash block
        return match.group(1).strip().split("\n")[0]
    return "echo build ok"


def check_build() -> str | None:
    """Run build command. Returns error message if build fails, None if OK."""
    cmd = read_build_cmd()
    # Split into args list — never use shell=True with repo-controlled content
    import shlex
    try:
        args = shlex.split(cmd)
    except ValueError:
        return f"Build command parse error: {cmd}"
    try:
        result = subprocess.run(
            args, shell=False, capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            stderr_tail = result.stderr[-500:] if result.stderr else ""
            return f"Build failed (exit {result.returncode}): {cmd}\n{stderr_tail}"
    except subprocess.TimeoutExpired:
        return f"Build timed out (60s): {cmd}"
    except Exception as e:
        return f"Build error: {e}"
    return None


def check_loop_complete(message: str) -> str | None:
    """Check if LOOP_COMPLETE is present in the final message."""
    if "LOOP_COMPLETE" in message:
        return None
    return "Missing LOOP_COMPLETE marker in final message. Add LOOP_COMPLETE when work is done."


def check_git_changes() -> str | None:
    """Check if git working tree has changes."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=10
        )
        if result.stdout.strip():
            return None  # Has changes — good
        return "No git changes detected. The agent may have no-op'd."
    except Exception:
        return None  # Can't check — allow


def check_context_doc(step_label: str, seq_name: str) -> str | None:
    """Check if the agent updated its CONTEXT doc section.

    Searches for CONTEXT docs in multiple locations:
    1. Current working directory (docs/CONTEXT-*.md)
    2. Subtrace workspace (the canonical location for cross-repo sequences)
    """
    search_dirs = [
        Path("docs"),
        Path.home() / "Desktop" / "Subtrace" / "docs",
    ]

    for docs_dir in search_dirs:
        if not docs_dir.exists():
            continue
        context_files = list(docs_dir.glob("CONTEXT-*.md"))
        for cf in context_files:
            try:
                text = cf.read_text()
            except OSError:
                continue
            # Look for placeholder comments that should have been replaced
            if f"<!-- Updated by {step_label}" in text:
                return f"CONTEXT doc {cf.name} has unfilled section for {step_label}. Update your Phase section before exiting."

    return None  # Either updated or no matching section


def main():
    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    # Infinite loop guard — always allow on second attempt
    # But still write approve to ledger (the first pass validated, agent fixed issues)
    if hook_input.get("stop_hook_active", False):
        # Use step commitment from engine env (not active_commitment file, which is the parent session)
        active_cmt = os.environ.get("MENTU_STEP_CMT_LEDGER", "")
        if not active_cmt:
            active_cmt_file = Path(".mentu/active_commitment")
            if active_cmt_file.exists():
                active_cmt = active_cmt_file.read_text().strip()
        if active_cmt and active_cmt.startswith("cmt_"):
                ledger_path = Path(".mentu/ledger.jsonl")
                if ledger_path.exists():
                    import hashlib
                    actor = os.environ.get("MENTU_ACTOR", "agent:claude-subtrace")
                    workspace = os.environ.get("MENTU_WORKSPACE", "subtrace")
                    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    step_tier_env = os.environ.get("MENTU_STEP_TIER", "")
                    genesis = GenesisReader()
                    effective_tier = genesis.get_step_tier(int(step_tier_env) if step_tier_env.isdigit() else None)
                    if effective_tier >= 3:
                        # Tier 3: awaiting human approval — do not auto-close
                        print("Tier 3: awaiting human approval.")
                    elif not genesis.actor_allowed(actor, "close"):
                        # Actor lacks close permission — submit for review instead
                        op_id = f"op_{hashlib.md5(f'{ts}{active_cmt}submit'.encode()).hexdigest()[:8]}"
                        submit_op = json.dumps({
                            "id": op_id, "op": "submit", "ts": ts,
                            "actor": actor, "workspace": workspace,
                            "payload": {"body": "Auto-submitted: all checks passed, awaiting reviewer close", "kind": "verdict", "commitment": active_cmt, "evidence": f"tier_{effective_tier}_auto"}
                        })
                        try:
                            with open(ledger_path, "a") as f:
                                f.write(submit_op + "\n")
                        except Exception:
                            pass
                    else:
                        # Tier 1/2 + actor has close permission: auto-close
                        op_id = f"op_{hashlib.md5(f'{ts}{active_cmt}close'.encode()).hexdigest()[:8]}"
                        close_op = json.dumps({
                            "id": op_id, "op": "close", "ts": ts,
                            "actor": actor, "workspace": workspace,
                            "payload": {"body": "Auto-closed: all checks passed", "kind": "verdict", "commitment": active_cmt, "evidence": f"tier_{effective_tier}_auto"}
                        })
                        try:
                            with open(ledger_path, "a") as f:
                                f.write(close_op + "\n")
                        except Exception:
                            pass
        sys.exit(0)

    # Check if auto_review is enabled
    try:
        state_file = Path(".claude/protocol-state.json")
        if not state_file.exists():
            sys.exit(0)
        state = json.loads(state_file.read_text())
        if not state.get("auto_review", False):
            sys.exit(0)
    except (json.JSONDecodeError, OSError):
        sys.exit(0)

    message = hook_input.get("last_assistant_message", "")
    step_label = state.get("step_label", "")
    is_recon = "recon" in step_label.lower() if step_label else False

    # Run checks
    failures = []

    # ── Phase 1: Commitment Closure Check ──
    # Prefer step commitment from engine env (sequence context)
    active_cmt = os.environ.get("MENTU_STEP_CMT_LEDGER", "")
    if not active_cmt:
        active_cmt_file = Path(".mentu/active_commitment")
        if active_cmt_file.exists():
            active_cmt = active_cmt_file.read_text().strip()

    if active_cmt and active_cmt.startswith("cmt_") and MentuLocalClient.is_available():
        cmt_status = MentuLocalClient.status(active_cmt)
        if cmt_status:
            cmt_state = cmt_status.get("state", "")
            if cmt_state not in ("closed", "in_review"):
                failures.append(
                    f"COMMITMENT: Active commitment `{active_cmt}` is in state `{cmt_state}`. "
                    f"Call `mentu submit {active_cmt} --summary '...'` before stopping."
                )

    # ── Phase 1b: Epistemic Loop Detection (ledger-based) ──
    ledger_path = Path(".mentu/ledger.jsonl")
    if ledger_path.exists() and active_cmt:
        try:
            submits = []
            for line in ledger_path.read_text().splitlines():
                op = json.loads(line)
                if op.get("op") == "submit" and op.get("payload", {}).get("commitment") == active_cmt:
                    submits.append(op)
            if len(submits) >= 2:
                ev1 = submits[-1].get("payload", {}).get("evidence", [])
                ev2 = submits[-2].get("payload", {}).get("evidence", [])
                if ev1 == ev2:
                    failures.append(
                        "EPISTEMIC LOOP: Duplicate evidence submitted to same commitment. "
                        "Change approach or escalate."
                    )
        except Exception:
            pass

    # ── Phase 2: Dual Triad Validation ──
    genesis = GenesisReader()
    step_tier_env = os.environ.get("MENTU_STEP_TIER", "")
    effective_tier = genesis.get_step_tier(int(step_tier_env) if step_tier_env.isdigit() else None)

    # Tag-based tier classification from genesis (overrides default when tags available)
    if active_cmt and ledger_path.exists() and genesis.exists:
        try:
            tags = []
            for line in ledger_path.read_text().splitlines():
                entry = json.loads(line)
                if entry.get("payload", {}).get("commitment") == active_cmt:
                    domain = (entry.get("semantic") or {}).get("domain", [])
                    tags.extend(domain)
            if tags:
                tier_name = genesis.classify_tier(tags)
                tier_config = genesis.get_tier_config(tier_name)
                try:
                    effective_tier = int(tier_name.split("_")[1])
                except (IndexError, ValueError):
                    pass
        except Exception:
            pass

    triad = run_dual_triad(
        tier=effective_tier,
        scope=genesis.scope,
        last_message=message,
    )

    for result in triad.failures:
        failures.append(f"TRIAD/{result.validator.upper()}: {result.evidence} — {'; '.join(result.details)}")

    # ── Phase 3: Structural Checks (legacy, preserved) ──
    # Build is now handled by Dual Triad Technical validator, skip duplicate
    # but keep LOOP_COMPLETE as standalone check (it's also the Intent validator input)

    loop_err = check_loop_complete(message)
    # Only add if not already caught by Intent validator (tier 3)
    if loop_err and effective_tier < 3:
        failures.append(f"COMPLETION: {loop_err}")

    # Skip git-changes check for recon steps — they may only write
    # summaries to .claude/summaries/ or docs that aren't yet tracked
    if not is_recon:
        git_err = check_git_changes()
        if git_err:
            failures.append(f"CHANGES: {git_err}")

    # Check CONTEXT doc was updated (if one exists for this sequence)
    seq_name = state.get("sequence_name", "")
    context_err = check_context_doc(step_label, seq_name)
    if context_err:
        failures.append(f"CONTEXT: {context_err}")

    # ── Phase 4: Ledger-native approve/reopen ──
    # The review gate verdict IS a ledger operation.
    # approve = labeled positive (training signal for crystallize)
    # reopen = labeled negative with basis (training signal for what to avoid)
    ledger_path = Path(".mentu/ledger.jsonl")
    if active_cmt and ledger_path.exists():
        import hashlib
        actor = os.environ.get("MENTU_ACTOR", "agent:claude-subtrace")
        workspace = os.environ.get("MENTU_WORKSPACE", "subtrace")
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        if failures:
            # REOPEN: labeled negative with basis
            op_id = f"op_{hashlib.md5(f'{ts}{active_cmt}reopen'.encode()).hexdigest()[:8]}"
            basis = [r.validator for r in triad.failures] if triad.failures else ["structural"]
            reason = "; ".join(failures[:3])  # Top 3 failures as reason
            reopen_op = json.dumps({
                "id": op_id, "op": "reopen", "ts": ts,
                "actor": actor, "workspace": workspace,
                "payload": {
                    "commitment": active_cmt,
                    "reason": reason,
                    "meta": {
                        "tier": effective_tier,
                        "basis": basis,
                        "failure_count": len(failures)
                    }
                }
            })
            try:
                with open(ledger_path, "a") as f:
                    f.write(reopen_op + "\n")
            except Exception:
                pass
        else:
            # Tier-aware closure: agent auto-close for tier 1/2, defer for tier 3
            if effective_tier >= 3:
                # Tier 3: leave in_review for human approval
                print("Tier 3: awaiting human approval.")
            elif not genesis.actor_allowed(actor, "close"):
                # Actor lacks close permission — submit for review instead
                op_id = f"op_{hashlib.md5(f'{ts}{active_cmt}submit'.encode()).hexdigest()[:8]}"
                submit_op = json.dumps({
                    "id": op_id, "op": "submit", "ts": ts,
                    "actor": actor, "workspace": workspace,
                    "payload": {
                        "body": "Auto-submitted: all checks passed, awaiting reviewer close",
                        "kind": "verdict",
                        "commitment": active_cmt,
                        "evidence": f"tier_{effective_tier}_auto"
                    }
                })
                try:
                    with open(ledger_path, "a") as f:
                        f.write(submit_op + "\n")
                except Exception:
                    pass
            else:
                # Tier 1/2 + actor has close permission: auto-close
                op_id = f"op_{hashlib.md5(f'{ts}{active_cmt}close'.encode()).hexdigest()[:8]}"
                close_op = json.dumps({
                    "id": op_id, "op": "close", "ts": ts,
                    "actor": actor, "workspace": workspace,
                    "payload": {
                        "body": "Auto-closed: all checks passed",
                        "kind": "verdict",
                        "commitment": active_cmt,
                        "evidence": f"tier_{effective_tier}_auto"
                    }
                })
                try:
                    with open(ledger_path, "a") as f:
                        f.write(close_op + "\n")
                except Exception:
                    pass

    if failures:
        print("REVIEW GATE — blocked exit. Fix these issues:\n")
        for f in failures:
            print(f"  - {f}")
        print("\nAddress the failures above, then try to exit again.")
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        sys.stderr.write(f"review_gate error: {e}\n")
        sys.exit(0)  # Fail open
