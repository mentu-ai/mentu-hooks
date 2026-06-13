#!/usr/bin/env python3
"""mentu_policy.adapters.gemini — the Gemini CLI adapter (M3, Commit C).

Replaces ``hooks/gemini_cir_hook.sh``. Gemini exposes only post-hoc lifecycle
events, so it CANNOT pre-refuse a tool call (capability ``gate: False``):

    BeforeAgent -> prompt_submit
    AfterAgent  -> stop
    AfterTool   -> post_tool       (post-hoc — observe only)

Encoder NEVER emits a block shape. A deny/ask is handed to the degradation path:
the encoder returns ``{}`` and the shim's ``_observe`` records a
``capability_degraded`` signal (fire-and-forget) marking that enforcement was
not possible — capability-honest, never a false "blocked" claim. (M4 lands the
full ladder.)

FIX vs legacy: like codex, the legacy hook's ``<<< "${INPUT:-{}}"`` parse always
collapsed the event to ``unknown``; this shim parses stdin correctly, so the
real event/body resolve — an intentional correction vs the pinned-buggy gemini
goldens."""
from __future__ import annotations

import os

from ..abi import AgentEvent
from . import io

AGENT = "gemini"
ACTOR = "agent:gemini"          # the legacy hook hardcodes the gemini actor
STDOUT_BEFORE_CAPTURE = False   # the legacy prints {} AFTER its capture

# native ``hook_event_name`` -> AgentEvent.event
_EVENT_MAP = {
    "BeforeAgent": "prompt_submit",
    "AfterAgent": "stop",
    "AfterTool": "post_tool",
}

# native event -> (CIR kind, body)
_KIND_BODY = {
    "BeforeAgent": ("prompt_submit", "gemini: agent starting"),
    "AfterAgent": ("session_stop", "gemini: agent completed"),
    "AfterTool": ("tool_use", "gemini: tool completed"),
}


def _native_event(data: dict) -> str:
    return data.get("hook_event_name", data.get("event", "unknown"))


def decode(native_stdin: dict, hook_event, environ=None, cwd=None) -> AgentEvent:
    data = native_stdin if isinstance(native_stdin, dict) else {}
    native = _native_event(data)
    event = _EVENT_MAP.get(native, "prompt_submit")
    return AgentEvent(agent=AGENT, event=event, actor=ACTOR)


def encode(decision, event):
    """Never a block shape — Gemini cannot enforce. Every verb -> {} (a deny/ask
    is degraded to a logged capability_degraded signal by the shim)."""
    return ("{}\n", 0)


def capture(native_stdin: dict, hook_event, environ, cwd: str):
    """The gemini event->KIND/BODY map (correctly parsed). domain = basename of
    $PWD; actor hardcoded ``agent:gemini``."""
    data = native_stdin if isinstance(native_stdin, dict) else {}
    native = _native_event(data)
    kind, body = _KIND_BODY.get(native, ("agent_event", "gemini: %s" % native))
    domain = os.path.basename(environ.get("PWD") or cwd or "")
    return (kind, body, domain, ACTOR)
