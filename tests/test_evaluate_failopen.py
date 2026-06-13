#!/usr/bin/env python3
"""Tests for mentu_policy.core.evaluate — routing and the structural fail-open
property: evaluate never raises into a caller; any internal fault -> PASS."""

import json
import unittest
from pathlib import Path

from mentu_policy import core
from mentu_policy.abi import AgentEvent, Decision, Verb, event_from_dict
from mentu_policy.core import evaluate

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "events"


class ExplodingAttrs:
    """Attribute access raises — simulates a hostile/broken event object."""

    def __getattribute__(self, name):
        raise RuntimeError("attribute access explodes")


class ExplodingEventProp:
    @property
    def event(self):
        raise ValueError("event property explodes")


def fuzzed_events():
    """Deterministic pool of >=200 malformed events (no randomness)."""
    cases = [
        None, 0, -1, 1.5, True, False, "stop", b"pre_tool",
        [], {}, (), set(), object(), type, Ellipsis,
        ExplodingAttrs(), ExplodingEventProp(),
        {"event": "stop"}, {"agent": "claude"},
    ]
    # Unknown event names route nowhere -> PASS
    for i in range(60):
        cases.append(AgentEvent(agent="claude", event="unknown_kind_%d" % i))
    # Wrong-typed / None fields
    for i in range(40):
        cases.append(AgentEvent(agent=None, event=i))
    for i in range(40):
        cases.append(AgentEvent(agent=i, event=None))
    # Unhashable event values make set membership raise TypeError
    for i in range(40):
        cases.append(AgentEvent(agent="x", event=["stop", i]))
    # Dicts masquerading as events (attribute access raises AttributeError)
    for i in range(20):
        cases.append({"event": "pre_tool", "n": i})
    # Objects whose attribute access raises
    for _ in range(20):
        cases.append(ExplodingAttrs())
    return cases


class TestFailOpenFuzz(unittest.TestCase):
    def test_fuzzed_events_always_pass_and_never_raise(self):
        cases = fuzzed_events()
        self.assertGreaterEqual(len(cases), 200)
        for case in cases:
            try:
                d = evaluate(case)
            except BaseException as exc:  # pragma: no cover - the failure we forbid
                self.fail("evaluate raised %r for %r" % (exc, case))
            self.assertIsInstance(d, Decision)
            self.assertEqual(d.verb, Verb.PASS)


class TestFailOpenEngineRaise(unittest.TestCase):
    def _assert_engine_failure_passes(self, register, restore, event_kind):
        def boom_engine(event, ctx):
            raise RuntimeError("engine exploded")

        register(boom_engine)
        try:
            d = evaluate(AgentEvent(agent="claude", event=event_kind))
            self.assertIsInstance(d, Decision)
            self.assertEqual(d.verb, Verb.PASS)
        finally:
            register(restore)

    def test_gate_engine_raise_fails_open(self):
        self._assert_engine_failure_passes(
            core.register_gate_engine, core._no_op_engine, "pre_tool")

    def test_supply_engine_raise_fails_open(self):
        self._assert_engine_failure_passes(
            core.register_supply_engine, core._no_op_engine, "session_start")

    def test_observe_engine_raise_fails_open(self):
        self._assert_engine_failure_passes(
            core.register_observe_engine, core._no_op_engine, "post_tool")

    def test_engine_returning_garbage_fails_open(self):
        core.register_gate_engine(lambda event, ctx: {"verb": "deny"})
        try:
            d = evaluate(AgentEvent(agent="claude", event="stop"))
            self.assertIsInstance(d, Decision)
            self.assertEqual(d.verb, Verb.PASS)
        finally:
            core.register_gate_engine(core._no_op_engine)


class TestRouting(unittest.TestCase):
    def test_events_route_to_the_declared_engine(self):
        hits = []

        def recorder(name):
            def engine(event, ctx):
                hits.append((name, event.event))
                return Decision.pass_()
            return engine

        core.register_gate_engine(recorder("gate"))
        core.register_supply_engine(recorder("supply"))
        core.register_observe_engine(recorder("observe"))
        try:
            for kind in ("stop", "subagent_stop", "pre_tool", "permission_request"):
                evaluate(AgentEvent(agent="claude", event=kind))
                self.assertEqual(hits[-1], ("gate", kind))
            for kind in ("session_start", "post_compact", "prompt_submit"):
                evaluate(AgentEvent(agent="claude", event=kind))
                self.assertEqual(hits[-1], ("supply", kind))
            for kind in ("post_tool", "post_tool_failure"):
                evaluate(AgentEvent(agent="claude", event=kind))
                self.assertEqual(hits[-1], ("observe", kind))
            # pre_compact / session_end route to no engine in M1
            before = len(hits)
            evaluate(AgentEvent(agent="claude", event="pre_compact"))
            evaluate(AgentEvent(agent="claude", event="session_end"))
            self.assertEqual(len(hits), before)
        finally:
            core.register_gate_engine(core._no_op_engine)
            core.register_supply_engine(core._no_op_engine)
            core.register_observe_engine(core._no_op_engine)


class TestGoldenVectors(unittest.TestCase):
    def test_each_canonical_fixture_returns_a_decision(self):
        files = sorted(FIXTURES.glob("*.json"))
        self.assertEqual(len(files), 11)
        for path in files:
            ev = event_from_dict(json.loads(path.read_text()))
            d = evaluate(ev)
            self.assertIsInstance(d, Decision)
            # M1 engines are no-ops: every canonical event verdict is PASS
            self.assertEqual(d.verb, Verb.PASS)


if __name__ == "__main__":
    unittest.main()
