#!/usr/bin/env python3
"""mentu_policy.adapters.claude — the Claude Code adapter (M3, Commit A).

Translates Claude Code's native hook I/O to/from the normalized ABI and
reproduces the four legacy wire shapes byte-for-byte (pinned by the golden
vectors in ``tests/fixtures/golden/``):

  1. Stop / SubagentStop deny      -> block text on stdout + exit 2
  2. PreToolUse / PermissionRequest -> ``hookSpecificOutput.permissionDecision``
                                       allow/deny/ask JSON; ``{}`` for pass
  3. session_start / post_compact   -> raw markdown on stdout
  4. pre_tool(Agent) inject         -> ``hookSpecificOutput.updatedInput.prompt`` JSON

The adapter holds NO policy: ``decode`` produces an ``AgentEvent``,
``build_ctx`` maps the native envelope (env vars + workspace files) into the
gate/supply ``ctx`` seam, and ``encode`` renders the returned ``Decision``.
``capture`` returns ``None`` — the Claude-surface hooks are not the
agent-lifecycle CIR-capture hooks (that is the universal/codex/cursor/gemini
adapters' job); their substrate reads happen inside ``supply_context``.

JSON is rendered with ``ensure_ascii=False`` to match ``jq``'s UTF-8 output
(the legacy ``jq -n`` hooks emit a literal em dash, not ``\\u2014``).
"""
from __future__ import annotations

import json
import os
from typing import Optional

from ..abi import AgentEvent, Decision, ToolRef, Verb
from ..genesis import GenesisReader
from ..probes import Probes
from ..substrate import Substrate
from . import io

AGENT = "claude"

# The universal/agent-lifecycle capture hooks emit their CIR signal AFTER the
# work; the Claude-surface hooks print their response and never capture, so the
# ordering flag is moot here (kept for the uniform shim contract).
STDOUT_BEFORE_CAPTURE = True

# Native hook identifiers (the shim's ``--event``) that this adapter accepts.
_STOP_SURFACE = {"stop", "subagent_stop"}
_PERMISSION_SURFACE = {"pre_tool", "permission_request"}
_SUPPLY_SURFACE = {"session_start", "post_compact", "prompt_submit"}


# ---------------------------------------------------------------------------
# decode — native stdin + hook identifier -> AgentEvent
# ---------------------------------------------------------------------------

def decode(native_stdin: dict, hook_event: str,
           environ=None, cwd: Optional[str] = None) -> AgentEvent:
    """Native Claude hook envelope -> ``AgentEvent``.

    ``hook_event`` is the native hook identifier the shim was invoked with
    (``stop``, ``subagent_stop``, ``pre_tool``, ``inject``, ``permission_request``,
    ``session_start``, ``post_compact``). Actor follows the io.py precedence;
    ``cwd`` defaults to the process CWD (the legacy hooks read workspace files
    relative to it)."""
    environ = environ if environ is not None else os.environ
    cwd = cwd if cwd is not None else os.getcwd()
    data = native_stdin if isinstance(native_stdin, dict) else {}
    actor = io.resolve_actor(environ)

    he = hook_event or ""
    if he in ("stop", "subagent_stop"):
        event = "stop" if he == "stop" else "subagent_stop"
        return AgentEvent(agent=AGENT, event=event, actor=actor, cwd=cwd,
                          message=data.get("last_assistant_message", ""))

    if he in ("pre_tool", "inject", "permission_request"):
        # ``inject`` and ``pre_tool`` are both PreToolUse natively; the shim
        # routes ``inject`` to the supply engine and ``pre_tool`` to the gate.
        event = "permission_request" if he == "permission_request" else "pre_tool"
        tool_input = data.get("tool_input")
        return AgentEvent(
            agent=AGENT, event=event, actor=actor, cwd=cwd,
            tool=ToolRef(name=data.get("tool_name", "") or "",
                         input=tool_input if isinstance(tool_input, dict) else {}),
        )

    if he == "session_start":
        return AgentEvent(agent=AGENT, event="session_start", actor=actor, cwd=cwd,
                          source=data.get("source"))

    if he == "post_compact":
        return AgentEvent(agent=AGENT, event="post_compact", actor=actor, cwd=cwd,
                          source=data.get("source"))

    # Fall back to a neutral, no-opinion event.
    return AgentEvent(agent=AGENT, event=he or "prompt_submit", actor=actor, cwd=cwd)


# ---------------------------------------------------------------------------
# build_ctx — native envelope -> the gate/supply ctx seam (NO policy here)
# ---------------------------------------------------------------------------

def _read_json(path: str):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


def _read_active_commitment(cwd: str) -> str:
    txt = _read_text(os.path.join(cwd, ".mentu", "active_commitment"))
    if txt is None:
        return ""
    cmt = txt.strip()
    return cmt if cmt.startswith("cmt_") else ""


def _isolation_active(cwd: str) -> bool:
    """Reproduce ``context_isolation_gate.is_active()``: protocol-state flag OR
    the deployed skill marker."""
    state = _read_json(os.path.join(cwd, ".claude", "protocol-state.json"))
    if isinstance(state, dict) and "context-isolation" in state.get("active_protocols", []):
        return True
    skill = os.path.join(cwd, ".claude", "skills", "context-isolation-protocol", "SKILL.md")
    return os.path.exists(skill)


def _stop_ctx(event: AgentEvent, raw_stdin: dict, environ, cwd: str) -> dict:
    """Map review_gate.py's env/file reads into the gate ctx (verbatim
    precedence: MENTU_STEP_CMT_LEDGER -> .mentu/active_commitment;
    MENTU_STEP_TIER digit -> genesis default; MENTU_ACTOR -> event.actor;
    MENTU_WORKSPACE -> 'subtrace')."""
    active_cmt = environ.get("MENTU_STEP_CMT_LEDGER", "") or _read_active_commitment(cwd)

    substrate = Substrate()
    commitment_state = None
    if active_cmt.startswith("cmt_") and substrate.socket_available():
        status = substrate.status(active_cmt)
        if status:
            commitment_state = status.get("state", "")

    tier_env = environ.get("MENTU_STEP_TIER", "")
    tier = int(tier_env) if tier_env.isdigit() else None

    return {
        "probes": Probes(cwd=cwd),
        "protocol_state": _read_json(os.path.join(cwd, ".claude", "protocol-state.json")),
        "stop_hook_active": bool(raw_stdin.get("stop_hook_active", False)),
        "active_commitment": active_cmt,
        "commitment_state": commitment_state,
        "tier": tier,
        "workspace": environ.get("MENTU_WORKSPACE", "subtrace"),
        "genesis": GenesisReader(workspace_dir=cwd),
        "claude_md_text": _read_text(os.path.join(cwd, "CLAUDE.md")),
        "ledger_path": os.path.join(cwd, ".mentu", "ledger.jsonl"),
        "scope": None,
        "context_docs": None,
        "cwd": cwd,
    }


def build_ctx(event: AgentEvent, hook_event: str, raw_stdin: dict,
              environ=None, cwd: Optional[str] = None) -> dict:
    """Assemble the ctx the core engines consume for this hook. Pure wiring —
    the verdict is computed by policy-core, not here."""
    environ = environ if environ is not None else os.environ
    cwd = cwd if cwd is not None else os.getcwd()
    he = hook_event or ""

    if he == "session_start":
        return {"substrate": Substrate(), "cwd": cwd}
    if he in ("post_compact", "inject"):
        return {"cwd": cwd}
    if he in ("pre_tool", "permission_request"):
        return {"probes": Probes(cwd=cwd),
                "ledger_path": os.path.join(cwd, ".mentu", "ledger.jsonl"),
                "cwd": cwd}
    if he == "subagent_stop":
        return {"isolation_active": _isolation_active(cwd)}
    if he == "stop":
        return _stop_ctx(event, raw_stdin, environ, cwd)
    return {}


# ---------------------------------------------------------------------------
# encode — Decision -> native Claude response (byte-for-byte)
# ---------------------------------------------------------------------------

def _json_block(obj: dict) -> str:
    """``jq -n``-equivalent pretty JSON: 2-space indent, insertion-ordered
    keys, UTF-8 (literal non-ASCII), trailing newline."""
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


def encode(decision: Decision, event: AgentEvent):
    """``Decision`` -> ``(stdout_str, exit_code)`` for Claude's native surface.
    Exhaustive over ``Verb`` so a missing case can never silently drop a verb."""
    kind = getattr(event, "event", "") or ""
    verb = decision.verb

    # (1) Stop / SubagentStop — exit-code + block-text surface.
    if kind in _STOP_SURFACE:
        if verb == Verb.DENY:
            return ((decision.reason or "") + "\n", 2)
        if verb == Verb.ALLOW:
            # tier-3 defer prints its reason; auto-close/submit print nothing.
            return (((decision.reason + "\n") if decision.reason else ""), 0)
        # PASS / ASK / INJECT / ANNOTATE -> allow exit, no output.
        return ("", 0)

    # (2)/(4) PreToolUse / PermissionRequest — JSON surface.
    if kind in _PERMISSION_SURFACE:
        if verb == Verb.INJECT:
            # (4) sub-agent prompt enrichment.
            prompt = (decision.updated_input or {}).get("prompt", "")
            return (_json_block({"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "updatedInput": {"prompt": prompt}}}), 0)
        if verb in (Verb.ALLOW, Verb.DENY, Verb.ASK):
            pd = {Verb.ALLOW: "allow", Verb.DENY: "deny", Verb.ASK: "ask"}[verb]
            return (_json_block({"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": pd,
                "permissionDecisionReason": decision.reason or ""}}), 0)
        # PASS / ANNOTATE -> no decision, fall through to the host default.
        return ("{}\n", 0)

    # (3) session_start / post_compact — raw markdown surface.
    if kind in _SUPPLY_SURFACE:
        if verb == Verb.INJECT and decision.inject_context:
            text = decision.inject_context
            # The legacy hooks emit ``print(content)``. post_compact's content
            # (compaction_reinjector) already carries its trailing "\n";
            # session_start's brief came back rstrip()'d by the supply core
            # (which trims for the cross-adapter prompt-prefix path), so restore
            # the one structural newline the legacy ``"\n".join(output)`` held.
            if kind == "session_start":
                text = text + "\n"
            return (text + "\n", 0)
        return ("", 0)

    # Unknown surface -> additive no-op.
    return ("{}\n", 0)


# ---------------------------------------------------------------------------
# capture — the Claude-surface hooks emit no agent-lifecycle CIR signal.
# ---------------------------------------------------------------------------

def capture(native_stdin: dict, hook_event: str, environ, cwd: str):
    """No agent-lifecycle capture for the Claude-surface hooks (their substrate
    reads, when any, happen inside ``supply_context``)."""
    return None
