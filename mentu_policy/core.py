#!/usr/bin/env python3
"""mentu_policy.core — evaluate(): the single AgentEvent -> Decision entry point.

Pure dispatch. This module never reads stdin, argv, or the environment —
all native I/O lives in the adapters (M3). Routing by event kind:

    stop | subagent_stop | pre_tool | permission_request  -> gate engine    (M2a)
    session_start | post_compact | prompt_submit          -> supply engine  (M2b)
    post_tool | post_tool_failure                         -> observe engine (M2b)

Fail-open is structural: the entire body is wrapped so any internal
exception returns Decision.pass_() — evaluate never raises into a caller.
An infrastructure fault in the harness never refuses the user's own work;
the worst case is a missed check, never a wrongful refusal.
"""

from .abi import Decision

GATE_EVENTS = {"stop", "subagent_stop", "pre_tool", "permission_request"}
SUPPLY_EVENTS = {"session_start", "post_compact", "prompt_submit"}
OBSERVE_EVENTS = {"post_tool", "post_tool_failure"}


def _no_op_engine(event, ctx):
    return Decision.pass_()


# Module-level registration hooks. No-ops until M2 registers the real
# gate / supply / observe engines.
_gate_engine = _no_op_engine
_supply_engine = _no_op_engine
_observe_engine = _no_op_engine


def register_gate_engine(engine):
    global _gate_engine
    _gate_engine = engine


def register_supply_engine(engine):
    global _supply_engine
    _supply_engine = engine


def register_observe_engine(engine):
    global _observe_engine
    _observe_engine = engine


def evaluate(event, ctx=None) -> Decision:
    """AgentEvent -> Decision. Never raises; any internal fault -> pass."""
    try:
        kind = event.event
        if kind in GATE_EVENTS:
            decision = _gate_engine(event, ctx)
        elif kind in SUPPLY_EVENTS:
            decision = _supply_engine(event, ctx)
        elif kind in OBSERVE_EVENTS:
            decision = _observe_engine(event, ctx)
        else:
            decision = Decision.pass_()
        if not isinstance(decision, Decision):
            return Decision.pass_()
        return decision
    except BaseException:
        return Decision.pass_()


# M2b: wire the real supply + observe engines, replacing the M1 no-ops above.
# Done at import time so `from mentu_policy import evaluate` yields a fully
# wired core. (The gate engine self-registers from mentu_policy.gates when an
# adapter imports it — M2a.) Imported here at the bottom to avoid any
# import-order coupling; neither module imports core, so there is no cycle.
from . import supply as _supply      # noqa: E402
from . import observe as _observe    # noqa: E402

register_supply_engine(_supply.supply_engine)
register_observe_engine(_observe.observe_engine)
