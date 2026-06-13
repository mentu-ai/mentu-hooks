#!/usr/bin/env python3
"""mentu_policy.adapters.shim — the generic adapter entry point (M3).

``main(argv)`` parses ``--agent X [--event Y] [native-event-arg]``, reads stdin
via io.py, runs ``decode -> evaluate -> encode`` through the named adapter, and
prints the native response. The outer guard is fail-open: ANY error prints
``{}`` and exits 0 — the harness never blocks the operator's own agent on its
own failure.

Substrate capture calls (the agent-lifecycle CIR signal) are fire-and-forget;
a slow or absent substrate can never delay the agent's response. The
adapter declares ``STDOUT_BEFORE_CAPTURE`` so the production path preserves each
legacy hook's stdout-vs-capture ordering.

The same ``run()`` core is reused in-process by the rewired Claude Python hooks
(``context_isolation_gate.py``, ``review_gate.py``) and by the parity tests, so
there is exactly one ``decode -> evaluate -> encode`` path.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from typing import Optional


# --- package bootstrap: resolve mentu_policy relative to THIS file so a
#     deployed copy (~/.mentu/hooks/mentu_policy/...) works after a future
#     installer run, without depending on the caller's CWD or PYTHONPATH. ---
_PKG_PARENT = str(Path(__file__).resolve().parent.parent.parent)
if _PKG_PARENT not in sys.path:
    sys.path.insert(0, _PKG_PARENT)

from mentu_policy import evaluate                     # noqa: E402
from mentu_policy import supply                        # noqa: E402
from mentu_policy import gates as _gates               # noqa: E402,F401
from mentu_policy import degrade                       # noqa: E402
from mentu_policy.substrate import Substrate           # noqa: E402
from mentu_policy.adapters import io                   # noqa: E402

# Importing mentu_policy.gates self-registers the M2a gate engine into
# core.evaluate's dispatch (the gate engine cannot register from core itself —
# gates imports core — so the adapter entry point is its wiring site, per the
# M1 design note in core.py). Without this, evaluate() routes gate events to the
# M1 no-op and every gate silently PASSes.


_ADAPTERS: dict = {}


def _get_adapter(name: str):
    """Import and cache the named adapter module (claude/codex/cursor/gemini/
    mentu). Raises ImportError for an unknown/not-yet-added agent."""
    if name not in _ADAPTERS:
        _ADAPTERS[name] = importlib.import_module("mentu_policy.adapters.%s" % name)
    return _ADAPTERS[name]


def _route(event, hook_event: str, ctx: dict):
    """Hand the event to policy-core. Almost everything goes through
    ``evaluate``; the one exception is the Agent-prompt inject, which fires on a
    pre_tool surface that ``evaluate`` routes to the gate — so the adapter
    dispatches it to the supply engine (still a policy-core function)."""
    if hook_event == "inject":
        return supply.supply_context(event, ctx)
    return evaluate(event, ctx)


def _fire_capture(args) -> None:
    """Fire the agent-lifecycle ``mentu cir capture`` (or capability-degraded
    signal). ``args`` is ``(kind, body, domain, actor)``. Fire-and-forget:
    never raises, never blocks beyond the substrate's own CLI timeout."""
    try:
        kind, body, domain, actor = args
        Substrate().capture_signal(kind, body, domain, actor)
    except Exception:
        pass


def _fire_degrade(signal: dict, environ, cwd: str) -> None:
    """Land a capability-degradation audit signal (``capability_degraded`` /
    ``supply_skipped``) returned by the M4 ladder through the SAME
    fire-and-forget capture path the lifecycle signal uses. Best-effort: never
    raises, never blocks beyond the substrate's own CLI timeout."""
    try:
        kind = signal.get("kind") or "capability_degraded"
        domain = os.path.basename(environ.get("PWD") or cwd or "")
        actor = io.resolve_actor(environ)
        body = json.dumps({
            "agent": signal.get("agent", ""),
            "requested_verb": signal.get("requested_verb", ""),
            "applied_verb": signal.get("applied_verb", ""),
            "reason": signal.get("reason", ""),
        })
        _fire_capture((kind, body, domain, actor))
    except Exception:
        pass


def _observe(adapter, agent: str, raw_stdin: dict, hook_event: str,
             environ, cwd: str, degrade_signal) -> None:
    """Run the adapter's agent-lifecycle capture, then fire any
    capability-degradation signal the M4 ladder produced for this event. Both
    are fire-and-forget substrate side-effects — they never alter the decision
    or block the agent. ``degrade_signal`` is computed in ``compute`` (between
    ``evaluate`` and ``encode``) so the down-shift drives BOTH the encoded
    response and this audit record from one decision."""
    try:
        cap = adapter.capture(raw_stdin, hook_event, environ, cwd) \
            if hasattr(adapter, "capture") else None
    except Exception:
        cap = None
    if cap is not None:
        _fire_capture(cap)

    if degrade_signal is not None:
        _fire_degrade(degrade_signal, environ, cwd)


def compute(agent: str, hook_event: Optional[str], stdin_text: str,
            environ, cwd: str):
    """``decode -> route -> encode`` for one event. Returns
    ``(stdout_str, exit_code, decision, capture_thunk)``; the thunk performs the
    fire-and-forget capture/degrade when called (so the caller controls the
    stdout-vs-capture ordering). ``decision`` is returned so a hook can persist
    a returned annotation (e.g. the review gate's ledger verdict)."""
    adapter = _get_adapter(agent)
    stdin_dict = io.parse_json(stdin_text)
    event = adapter.decode(stdin_dict, hook_event, environ, cwd)
    ctx = adapter.build_ctx(event, hook_event, stdin_dict, environ, cwd) \
        if hasattr(adapter, "build_ctx") else {}
    decision = _route(event, hook_event, ctx)

    # M4 capability ladder — reconcile the policy-core verdict with what THIS
    # agent can actually enforce, BEFORE encoding. A verb the agent cannot honor
    # (Gemini deny/ask; an inject on a constrained channel) is down-shifted to a
    # verb it can, so the encoder never emits a false "blocked" claim. Any loss
    # of capability returns an audit signal the thunk fires fire-and-forget.
    applied, degrade_signal = degrade.apply_capability(decision, agent, event)
    stdout, code = adapter.encode(applied, event)

    def _thunk():
        _observe(adapter, agent, stdin_dict, hook_event, environ, cwd, degrade_signal)

    return stdout, code, applied, _thunk


def run(agent: str, hook_event: Optional[str], stdin_text: str,
        environ=None, cwd: Optional[str] = None):
    """In-process entry: ``(stdout_str, exit_code)`` with the capture fired
    inline (ordering is immaterial for the Claude-surface callers, which do not
    capture). Used by the rewired Python hooks and the parity tests."""
    environ = environ if environ is not None else os.environ
    cwd = cwd if cwd is not None else os.getcwd()
    stdout, code, _decision, thunk = compute(agent, hook_event, stdin_text, environ, cwd)
    thunk()
    return stdout, code


def run_with_decision(agent: str, hook_event: Optional[str], stdin_text: str,
                      environ=None, cwd: Optional[str] = None):
    """As ``run`` but also returns the ``Decision`` — for hooks that persist a
    returned annotation (the review gate appends its ledger verdict)."""
    environ = environ if environ is not None else os.environ
    cwd = cwd if cwd is not None else os.getcwd()
    stdout, code, decision, thunk = compute(agent, hook_event, stdin_text, environ, cwd)
    thunk()
    return stdout, code, decision


def _parse_args(argv):
    """Parse ``--agent X [--event Y] [native-event-arg]``. The trailing
    positional is the native event identifier some agents pass as argv (Cursor's
    ``$1``); when present it becomes ``hook_event`` unless ``--event`` was given."""
    agent = None
    event = None
    positional = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--agent" and i + 1 < len(argv):
            agent = argv[i + 1]; i += 2; continue
        if tok == "--event" and i + 1 < len(argv):
            event = argv[i + 1]; i += 2; continue
        positional.append(tok); i += 1
    if event is None and positional:
        event = positional[0]
    return agent, event


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        agent, hook_event = _parse_args(argv)
        if not agent:
            sys.stdout.write("{}\n")
            return 0
        stdin_text = sys.stdin.read()
        adapter = _get_adapter(agent)
        stdout, code, _decision, thunk = compute(agent, hook_event, stdin_text,
                                                 dict(os.environ), os.getcwd())
        if getattr(adapter, "STDOUT_BEFORE_CAPTURE", True):
            sys.stdout.write(stdout)
            sys.stdout.flush()
            thunk()
        else:
            thunk()
            sys.stdout.write(stdout)
        return code
    except SystemExit:
        raise
    except BaseException:
        # Fail-open: never block the operator's agent on the harness's fault.
        try:
            sys.stdout.write("{}\n")
        except Exception:
            pass
        return 0


if __name__ == "__main__":
    sys.exit(main())
