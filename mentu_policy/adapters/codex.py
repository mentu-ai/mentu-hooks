#!/usr/bin/env python3
"""mentu_policy.adapters.codex — the OpenAI Codex CLI adapter (M3, Commit C).

Replaces ``hooks/codex_cir_hook.sh``. Event key is ``type`` (falling back to
``hook_event_name`` / ``event``). Normalization table:

    task_started        -> prompt_submit
    exec_command_begin  -> pre_tool
    _approval_request   -> permission_request
    Stop                -> stop
    PostToolUse         -> post_tool
    PostToolUseFailure  -> post_tool_failure

Encoder: allow/deny -> the approval approve/deny shape; pass -> ``{}``.

FIX vs legacy: the legacy hook parsed stdin with ``<<< "${INPUT:-{}}"``, which
bash mis-expands (``${INPUT:-{}`` + a stray ``}``) so the JSON is corrupted and
the event/actor ALWAYS collapse to ``unknown``. This shim parses stdin correctly
(io.py), so the real event/actor resolve — an intentional correction that
legitimately differs from the pinned-buggy codex goldens (see test_parity_agents
and the Commit C message)."""
from __future__ import annotations

import os
from typing import Optional, Tuple

from ..abi import AgentEvent, ToolRef, Verb
from . import io

AGENT = "codex"
ACTOR = "agent:codex"          # the legacy hook hardcodes the codex actor
STDOUT_BEFORE_CAPTURE = False  # the legacy prints {} AFTER its capture

# native ``type`` -> AgentEvent.event
_EVENT_MAP = {
    "task_started": "prompt_submit",
    "UserPromptSubmit": "prompt_submit",
    "exec_command_begin": "pre_tool",
    "_approval_request": "permission_request",
    "PermissionRequest": "permission_request",
    "Stop": "stop",
    "PostToolUse": "post_tool",
    "PostToolUseFailure": "post_tool_failure",
}

# native ``type`` -> (CIR kind, body)
_KIND_BODY = {
    "task_started": ("prompt_submit", "codex: task started"),
    "UserPromptSubmit": ("prompt_submit", "codex: task started"),
    "exec_command_begin": ("command_exec", "codex: command execution"),
    "_approval_request": ("permission_gate", "codex: approval requested"),
    "PermissionRequest": ("permission_gate", "codex: approval requested"),
    "Stop": ("session_stop", "codex: session ended"),
    "PostToolUse": ("tool_use", "codex: tool completed"),
    "PostToolUseFailure": ("tool_failure", "codex: tool failed"),
}


def _native_event(data: dict) -> str:
    return data.get("type", data.get("hook_event_name", data.get("event", "unknown")))


def decode(native_stdin: dict, hook_event, environ=None, cwd=None) -> AgentEvent:
    data = native_stdin if isinstance(native_stdin, dict) else {}
    native = _native_event(data)
    event = _EVENT_MAP.get(native, "prompt_submit")
    tool_input = data.get("tool_input")
    return AgentEvent(
        agent=AGENT, event=event, actor=ACTOR,
        tool=ToolRef(name=data.get("tool_name", "") or "",
                     input=tool_input if isinstance(tool_input, dict) else {}),
    )


def encode(decision, event):
    """allow/deny -> approval shape; ask -> leave pending ({}); pass -> {}."""
    verb = decision.verb
    if verb == Verb.ALLOW:
        return ('{"decision":"approve"}\n', 0)
    if verb == Verb.DENY:
        reason = (decision.reason or "").replace('"', '\\"')
        return ('{"decision":"deny","reason":"%s"}\n' % reason, 0)
    # ask -> leave the approval pending; inject/annotate/pass -> no decision.
    return ("{}\n", 0)


def capture(native_stdin: dict, hook_event, environ, cwd: str):
    """The codex event->KIND/BODY map (correctly parsed). domain = basename of
    $PWD; actor hardcoded ``agent:codex`` (matching the legacy hook)."""
    data = native_stdin if isinstance(native_stdin, dict) else {}
    native = _native_event(data)
    kind, body = _KIND_BODY.get(native, ("agent_event", "codex: %s" % native))
    domain = os.path.basename(environ.get("PWD") or cwd or "")
    return (kind, body, domain, ACTOR)
