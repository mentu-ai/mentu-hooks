#!/usr/bin/env python3
"""mentu_policy.adapters.cursor — the Cursor agent adapter (M3, Commit C).

Replaces ``hooks/cursor_cir_hook.sh``. The event arrives as argv (``$1``):

    Start | beforeSubmitPrompt   -> prompt_submit
    stop                         -> stop
    beforeShellExecution         -> permission_request
    beforeMCPExecution           -> permission_request
    PermissionRequest            -> permission_request

Encoder — THE behavior change of this milestone. The legacy hook hardcoded
``{"continue":true}`` on every permission event; here the verdict comes from
``evaluate()`` via the gate:

    deny / ask           -> {"continue":false,"reason":…}
    allow / pass         -> {"continue":true}     (permission events)
    (non-permission)     -> {}

With an absent substrate the trust math lands in the middle band -> pass ->
``{"continue":true}`` — byte-identical to the old hardcode. A refusal now
requires affirmative ledger evidence (a real verdict), not a default.

The pass-case literal ``{"continue":true}`` is emitted verbatim (not via
json.dumps, which would insert a space after the colon) to stay byte-equal to
the legacy ``echo``."""
from __future__ import annotations

import json
import os

from ..abi import AgentEvent, ToolRef, Verb
from . import io

AGENT = "cursor"
ACTOR = "agent:cursor"          # the legacy hook hardcodes the cursor actor
STDOUT_BEFORE_CAPTURE = True    # the legacy prints the response before capture

_PERMISSION_EVENTS = {"beforeShellExecution", "beforeMCPExecution", "PermissionRequest"}

# native event (argv) -> AgentEvent.event
_EVENT_MAP = {
    "Start": "prompt_submit",
    "beforeSubmitPrompt": "prompt_submit",
    "stop": "stop",
    "Stop": "stop",
    "beforeShellExecution": "permission_request",
    "beforeMCPExecution": "permission_request",
    "PermissionRequest": "permission_request",
}

# native event (argv) -> (CIR kind, body)
_KIND_BODY = {
    "Start": ("prompt_submit", "cursor: prompt submitted"),
    "beforeSubmitPrompt": ("prompt_submit", "cursor: prompt submitted"),
    "stop": ("session_stop", "cursor: session ended"),
    "Stop": ("session_stop", "cursor: session ended"),
    "beforeShellExecution": ("permission_gate", "cursor: shell execution requested"),
    "PermissionRequest": ("permission_gate", "cursor: shell execution requested"),
    "beforeMCPExecution": ("permission_gate", "cursor: MCP execution requested"),
}


def decode(native_stdin: dict, hook_event, environ=None, cwd=None) -> AgentEvent:
    native = hook_event or "unknown"
    event = _EVENT_MAP.get(native, "prompt_submit")
    return AgentEvent(agent=AGENT, event=event, actor=ACTOR,
                      tool=ToolRef(name="", input={}))


def encode(decision, event):
    """Permission events carry the verdict; everything else is additive ({})."""
    if getattr(event, "event", "") == "permission_request":
        if decision.verb in (Verb.DENY, Verb.ASK):
            return ('{"continue":false,"reason":%s}\n'
                    % json.dumps(decision.reason or ""), 0)
        # allow / pass / inject / annotate -> approve (legacy default).
        return ('{"continue":true}\n', 0)
    return ("{}\n", 0)


def capture(native_stdin: dict, hook_event, environ, cwd: str):
    """The cursor event->KIND/BODY map. domain = basename of $PWD; actor
    hardcoded ``agent:cursor`` (matching the legacy hook). The body carries no
    actor prefix (the legacy cursor body is already ``cursor: …``)."""
    native = hook_event or "unknown"
    kind, body = _KIND_BODY.get(native, ("agent_event", "cursor: %s" % native))
    domain = os.path.basename(environ.get("PWD") or cwd or "")
    return (kind, body, domain, ACTOR)
