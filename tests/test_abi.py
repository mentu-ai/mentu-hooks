#!/usr/bin/env python3
"""Tests for mentu_policy.abi — the AgentEvent / Decision contract."""

import json
import unittest
from pathlib import Path

from mentu_policy.abi import (
    EVENTS,
    AgentEvent,
    Decision,
    ToolRef,
    Verb,
    event_from_dict,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "events"

CANONICAL_EVENTS = {
    "session_start", "prompt_submit", "pre_tool", "post_tool", "post_tool_failure",
    "permission_request", "pre_compact", "post_compact", "subagent_stop", "stop", "session_end",
}

CANONICAL_VERBS = {"allow", "deny", "ask", "pass", "inject", "annotate"}


class TestVerb(unittest.TestCase):
    def test_exactly_six_values(self):
        self.assertEqual({v.value for v in Verb}, CANONICAL_VERBS)
        self.assertEqual(len(list(Verb)), 6)

    def test_verb_is_str_enum(self):
        self.assertIsInstance(Verb.PASS, str)
        self.assertEqual(Verb.DENY.value, "deny")


class TestEvents(unittest.TestCase):
    def test_exactly_eleven_kinds(self):
        self.assertEqual(EVENTS, CANONICAL_EVENTS)
        self.assertEqual(len(EVENTS), 11)


class TestDecisionFactories(unittest.TestCase):
    def test_default_is_pass(self):
        d = Decision()
        self.assertEqual(d.verb, Verb.PASS)
        self.assertEqual(d.reason, "")
        self.assertIsNone(d.inject_context)
        self.assertIsNone(d.updated_input)
        self.assertIsNone(d.annotate)

    def test_deny_carries_reason(self):
        d = Decision.deny("r")
        self.assertEqual(d.verb, Verb.DENY)
        self.assertEqual(d.reason, "r")

    def test_allow_and_ask(self):
        self.assertEqual(Decision.allow().verb, Verb.ALLOW)
        self.assertEqual(Decision.allow("ok").reason, "ok")
        a = Decision.ask("needs human")
        self.assertEqual(a.verb, Verb.ASK)
        self.assertEqual(a.reason, "needs human")

    def test_pass_factory(self):
        d = Decision.pass_()
        self.assertEqual(d.verb, Verb.PASS)

    def test_supply_prompt_sets_updated_input(self):
        d = Decision.supply(prompt="enriched prompt")
        self.assertEqual(d.verb, Verb.INJECT)
        self.assertEqual(d.updated_input, {"prompt": "enriched prompt"})
        self.assertIsNone(d.inject_context)

    def test_supply_md_sets_inject_context(self):
        d = Decision.supply(md="## session brief")
        self.assertEqual(d.verb, Verb.INJECT)
        self.assertEqual(d.inject_context, "## session brief")
        self.assertIsNone(d.updated_input)

    def test_note_sets_annotate(self):
        d = Decision.note("tool_use", "ran Bash")
        self.assertEqual(d.verb, Verb.ANNOTATE)
        self.assertEqual(d.annotate, {"kind": "tool_use", "body": "ran Bash"})


class TestAgentEventDefaults(unittest.TestCase):
    def test_defaults(self):
        e = AgentEvent(agent="claude", event="stop")
        self.assertEqual(e.session_id, "unknown")
        self.assertEqual(e.actor, "agent:unknown")
        self.assertEqual(e.cwd, "")
        self.assertIsNone(e.tool)
        self.assertIsNone(e.prompt)
        self.assertIsNone(e.message)
        self.assertIsNone(e.source)

    def test_tool_ref_defaults(self):
        t = ToolRef(name="Bash")
        self.assertEqual(t.input, {})
        self.assertIsNone(t.output)
        self.assertIsNone(t.exit_code)


class TestFixturesDecode(unittest.TestCase):
    def test_every_fixture_decodes_to_valid_event(self):
        files = sorted(FIXTURES.glob("*.json"))
        self.assertEqual(len(files), 11, "expected one fixture per event kind")
        kinds = set()
        for path in files:
            data = json.loads(path.read_text())
            ev = event_from_dict(data)
            self.assertIsInstance(ev, AgentEvent)
            self.assertIn(ev.event, EVENTS)
            self.assertIsInstance(ev.agent, str)
            self.assertIsInstance(ev.session_id, str)
            self.assertIsInstance(ev.actor, str)
            if ev.tool is not None:
                self.assertIsInstance(ev.tool, ToolRef)
                self.assertIsInstance(ev.tool.input, dict)
            kinds.add(ev.event)
        self.assertEqual(kinds, EVENTS, "fixtures must cover all 11 event kinds")

    def test_tool_fixture_round_trip(self):
        data = json.loads((FIXTURES / "post_tool.json").read_text())
        ev = event_from_dict(data)
        self.assertEqual(ev.tool.name, "Bash")
        self.assertEqual(ev.tool.exit_code, 0)
        self.assertEqual(ev.tool.input, {"command": "ls -la"})


if __name__ == "__main__":
    unittest.main()
