#!/usr/bin/env python3
"""mentu_policy.adapters.mentu — the mentu adapter.

This module fills two mentu-native roles that share one package home:

1. **Universal agent-lifecycle hook** (M3, Commit C) — ``decode`` / ``encode`` /
   ``capture`` below. Replaces ``hooks/mentu_agent_hook.sh``: an observe-only
   hook that normalizes events from ANY tool into a CIR signal with per-actor
   attribution. The response is always ``{}`` (it never gates); the work is the
   agent-lifecycle capture. Byte/argv-faithful to the legacy hook, including its
   ``read``-parsing quirk: the legacy joins ``event\\ttool\\tsid\\tcwd`` and reads
   it with the default IFS, which COLLAPSES empty fields — so a tool-less event
   (UserPromptSubmit / Stop / unknown) shifts the cwd out of range and the
   capture ``--domain`` becomes empty. ``_read_fields`` reproduces that collapse
   exactly (the goldens pin it). Actor follows the io.py precedence; the capture
   body is ``"<actor>: <body>"`` (the universal hook prefixes the actor; the
   codex/cursor/gemini hooks do not).

2. **HarnessV1 bridge** (M5) — ``on_harness_event`` / ``_to_agent_event`` /
   ``_to_harness_control`` below. Maps mentu's own HarnessV1 stream parts onto
   the SAME ``AgentEvent`` the foreign adapters emit, runs them through the SAME
   ``core.evaluate``, and renders the verdict back as a HarnessV1 control call —
   so a single policy core governs both foreign (Claude/Codex/Cursor/Gemini) and
   native (mentu) runs. mentu is the one all-``True`` capability row, so no
   degradation applies. A ``deny`` maps onto HarnessV1's EXISTING approval
   boundary (``submitToolApproval`` with ``approved=false``) — a clean refuse at
   the turn boundary, never a mid-flight abort.

   **Deferral.** The HarnessV1 runtime itself (BUILD-Mentu-Harness-Assimilation-
   v1.0 §1B + P1.3) is spec-only — nothing emits these stream parts yet. This
   bridge therefore ships as a PURE module over committed fixture dicts
   (``tests/fixtures/harness_v1/``); it is NOT wired into the live runner, the
   daemon, or any socket, and importing it performs no I/O. The integration seam
   is ``on_harness_event(hv1_part, ctx)``: when §1B lands, the runner calls it
   with each HarnessV1 stream part and acts on the returned control dict."""
from __future__ import annotations

import os
from typing import List, Optional, Tuple

from ..abi import AgentEvent, ToolRef, Verb
from . import io
from .. import core
from .. import gates as _gates  # noqa: F401

# Importing mentu_policy.gates self-registers the M2a gate engine into
# core.evaluate's dispatch (the gate engine cannot register from core itself —
# gates imports core — so each policy-core entry point is its wiring site, like
# mentu_policy.adapters.shim). Without it, on_harness_event's gate events would
# route to the M1 no-op and every gate silently PASS. Pure import: the
# registration is in-memory; no socket / subprocess / file is touched.

AGENT = "mentu"
STDOUT_BEFORE_CAPTURE = True   # the legacy prints {} BEFORE its capture


def _read_fields(values: List[str], n: int = 4) -> List[str]:
    """Simulate bash ``read -r v1..vN`` with the default IFS over
    ``"\\t".join(values)``: runs of whitespace collapse and empty fields drop,
    so positions shift; the last variable absorbs any remainder."""
    parts = "\t".join(values).split()
    if len(parts) <= n:
        return parts + [""] * (n - len(parts))
    return parts[:n - 1] + [" ".join(parts[n - 1:])]


def _native_fields(data: dict) -> Tuple[str, str, str, str]:
    """(EVENT, TOOL_NAME, SESSION_ID, CWD) after the legacy read-collapse."""
    event = data.get("hook_event_name", data.get("event", "unknown"))
    tool = data.get("tool_name", "")
    sid = data.get("session_id", "unknown")
    cwd = data.get("cwd", "")
    fields = _read_fields([str(event), str(tool), str(sid), str(cwd)])
    return fields[0], fields[1], fields[2], fields[3]


def _kind_body(event: str, tool_name: str) -> Optional[Tuple[str, str]]:
    """The legacy event->KIND/BODY case map. ``None`` == the legacy ``echo '{}';
    exit 0`` skip (no capture) for dedup'd file ops and read-only tools."""
    if event in ("UserPromptSubmit", "BeforeAgent"):
        return ("prompt_submit", "prompt submitted (%s)" % (tool_name or "session"))
    if event == "PostToolUse":
        if tool_name in ("Edit", "Write", "MultiEdit"):
            return None   # file_change already emitted — skip duplicate
        if tool_name == "Bash":
            return ("command_exec", "bash command executed")
        if tool_name == "Agent":
            return ("agent_spawn", "sub-agent spawned")
        if tool_name in ("Read", "Glob", "Grep"):
            return None   # read-only — skip to reduce noise
        return ("tool_use", "tool: %s" % tool_name)
    if event == "PostToolUseFailure":
        return ("tool_failure", "FAILED: %s" % tool_name)
    if event in ("Stop", "AfterAgent"):
        return ("session_stop", "session ended")
    if event == "PermissionRequest":
        return ("permission_gate", "permission requested: %s" % tool_name)
    if event == "AfterTool":
        return ("tool_use", "tool completed (%s)" % (tool_name or "unknown"))
    return ("agent_event", "event: %s" % event)


def decode(native_stdin: dict, hook_event, environ=None, cwd=None) -> AgentEvent:
    """Observe-only: the response is always ``{}``; a no-op event keeps the
    route from doing any work (the verdict is unused)."""
    environ = environ if environ is not None else os.environ
    return AgentEvent(agent=AGENT, event="session_end",
                      actor=io.resolve_actor(environ))


def encode(decision, event):
    """The universal hook always returns success immediately (``echo '{}'``)."""
    return ("{}\n", 0)


def capture(native_stdin: dict, hook_event, environ, cwd: str):
    """Reproduce the legacy ``mentu cir capture`` argv: kind/body from the
    event map, body prefixed with the resolved actor, domain = basename of the
    (collapsed) stdin cwd. ``None`` for the skip cases."""
    data = native_stdin if isinstance(native_stdin, dict) else {}
    event, tool_name, _sid, cwd_field = _native_fields(data)
    km = _kind_body(event, tool_name)
    if km is None:
        return None
    kind, body = km
    actor = io.resolve_actor(environ)
    domain = os.path.basename(cwd_field)
    return (kind, "%s: %s" % (actor, body), domain, actor)


# ===========================================================================
# M5 — HarnessV1 bridge
#
#   HarnessV1 stream part --_to_agent_event--> AgentEvent
#                         --core.evaluate----> Decision
#                         --_to_harness_control--> HarnessV1 control dict
#
# Same core, same verdicts as the foreign adapters. See the module docstring
# for the deferral note; the integration seam is ``on_harness_event``. Pure:
# every function here is a total map over its dict arguments — no I/O.
# ===========================================================================


def _tool_ref(part: dict, *, with_output: bool = False) -> ToolRef:
    """Build a ``ToolRef`` from a HarnessV1 tool part (``tool-call`` /
    ``tool-approval-request`` / ``tool-result``). ``input`` is the AI-SDK field
    (``args`` accepted as a fallback); ``ToolRef.input`` is always a dict. When
    ``with_output`` is set, the result's output text and exit code are lifted too
    (the post_tool surface)."""
    name = part.get("toolName") or part.get("name") or ""
    raw = part.get("input")
    if not isinstance(raw, dict):
        raw = part.get("args") if isinstance(part.get("args"), dict) else {}
    ref = ToolRef(name=name, input=raw)
    if with_output:
        out = part.get("output")
        if isinstance(out, str):
            ref.output = out
        elif out is not None:
            ref.output = str(out)
        ec = part.get("exitCode")
        if not isinstance(ec, int):
            ec = part.get("exit_code")
        if isinstance(ec, int):
            ref.exit_code = ec
    return ref


def _to_agent_event(hv1_part: dict, ctx=None):
    """HarnessV1 stream part -> normalized ``AgentEvent`` (or ``None`` for a part
    the policy core has no opinion on). Implements the §M5 mapping table:

        doStart                              -> session_start
        message (role=user)                  -> prompt_submit
        tool-call (providerExecuted falsy)   -> pre_tool
        tool-approval-request                -> permission_request
        tool-result                          -> post_tool
        tool-result (isError) / tool-error   -> post_tool_failure
        compaction (phase=post / else)       -> post_compact / pre_compact
        finish                               -> stop
        doStop                               -> session_end
        anything else                        -> None

    ``ctx`` (optional) carries the native run's envelope; ``actor`` / ``cwd`` /
    ``session_id`` are lifted from it when present (a native mentu run is
    ``agent:mentu`` by default). The adapter holds NO policy — it only
    normalizes the shape."""
    if not isinstance(hv1_part, dict):
        return None
    cd = ctx if isinstance(ctx, dict) else {}
    session_id = cd.get("session_id", "unknown")
    actor = cd.get("actor", "agent:mentu")
    cwd = cd.get("cwd", "")
    ptype = hv1_part.get("type")

    def ev(event, **kw):
        return AgentEvent(agent=AGENT, event=event, session_id=session_id,
                          actor=actor, cwd=cwd, **kw)

    if ptype == "doStart":
        return ev("session_start", source=hv1_part.get("source"))
    if ptype == "message" and hv1_part.get("role") == "user":
        text = hv1_part.get("text")
        if not isinstance(text, str):
            content = hv1_part.get("content")
            text = content if isinstance(content, str) else None
        return ev("prompt_submit", prompt=text)
    if ptype == "tool-call":
        # providerExecuted=true means the model provider already ran the tool;
        # only a host-dispatched call reaches the pre-action gate.
        if hv1_part.get("providerExecuted"):
            return None
        return ev("pre_tool", tool=_tool_ref(hv1_part))
    if ptype == "tool-approval-request":
        return ev("permission_request", tool=_tool_ref(hv1_part))
    if ptype == "tool-result":
        if hv1_part.get("isError") or hv1_part.get("error"):
            return ev("post_tool_failure", tool=_tool_ref(hv1_part, with_output=True))
        return ev("post_tool", tool=_tool_ref(hv1_part, with_output=True))
    if ptype == "tool-error":
        return ev("post_tool_failure", tool=_tool_ref(hv1_part, with_output=True))
    if ptype == "compaction":
        post = hv1_part.get("phase") == "post"
        return ev("post_compact" if post else "pre_compact",
                  source=hv1_part.get("source"))
    if ptype == "finish":
        msg = hv1_part.get("text")
        if not isinstance(msg, str):
            other = hv1_part.get("message")
            msg = other if isinstance(other, str) else ""
        return ev("stop", message=msg)
    if ptype == "doStop":
        return ev("session_end")
    return None


def _to_harness_control(decision, part=None):
    """``Decision`` -> HarnessV1 control call (or ``None`` for a no-op verb).

    mentu is full-capability (all-True registry row), so no degradation applies.
    A DENY is a clean refuse at the EXISTING approval boundary
    (``submitToolApproval`` ``approved=False``) — never a mid-flight abort."""
    verb = decision.verb
    if verb is Verb.DENY:
        return {"control": "submitToolApproval", "approved": False,
                "reason": decision.reason}
    if verb is Verb.ALLOW:
        return {"control": "submitToolApproval", "approved": True}
    if verb is Verb.ASK:
        return {"control": "deferToOperator", "reason": decision.reason}
    if verb is Verb.INJECT:
        return {"control": "supplyContext", "context": decision.inject_context,
                "updatedInput": decision.updated_input}
    if verb is Verb.ANNOTATE:
        # observe-only; the runner already records to CIR, so this is a no-op
        # control (the audit signal is persisted out-of-band).
        return None
    return None  # PASS — no opinion; fall through to the runner's default


def on_harness_event(hv1_part: dict, ctx=None):
    """The integration seam. HarnessV1 stream part -> ``AgentEvent`` -> the SAME
    ``core.evaluate`` the foreign adapters call -> HarnessV1 control dict (or
    ``None`` when the part is unmapped or the verdict is a no-op).

    ``ctx`` is the run's gate/supply context (probes, protocol_state, tier,
    actor, ...) exactly as the foreign adapters assemble it; it is forwarded to
    ``core.evaluate`` so a native run gets the same checks a foreign one would.

    Deferred: no runner calls this yet — the HarnessV1 runtime is spec-only (see
    the module docstring). It is exercised here only over committed fixtures."""
    ev = _to_agent_event(hv1_part, ctx)
    if ev is None:
        return None
    decision = core.evaluate(ev, ctx)
    return _to_harness_control(decision, hv1_part)
