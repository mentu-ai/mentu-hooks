#!/usr/bin/env python3
"""Tests for mentu_policy.degrade — the M4 capability degradation ladder.

apply_capability(decision, agent, event) -> (applied_decision, signal_or_none):

  * deny/ask on a non-gating agent (Gemini)  -> annotate + capability_degraded
  * inject on a "partial" channel             -> prompt-prepend, no signal
  * inject where no channel exists at all     -> skip (pass) + supply_skipped
  * a full-capability agent / unknown agent   -> passthrough, no signal, no raise

Hermetic + stdlib-only: every case builds its own AgentEvent and Decision; the
ladder is a pure function (no substrate, no I/O), so no sandbox is needed."""

import unittest
from unittest import mock

from mentu_policy import capabilities
from mentu_policy.abi import AgentEvent, Decision, Verb
from mentu_policy.degrade import apply_capability

_DEGRADE_FIELDS = ("agent", "requested_verb", "applied_verb", "reason")


def _ev(agent, event="post_tool"):
    return AgentEvent(agent=agent, event=event, actor="agent:%s" % agent)


class TestGateDownshift(unittest.TestCase):
    """deny / ask on Gemini (gate:False) → annotate + one capability_degraded."""

    def test_gemini_deny_downshifts_to_annotate_with_signal(self):
        applied, signal = apply_capability(Decision.deny("low CIR trust"),
                                           "gemini", _ev("gemini"))
        # Applied verb is annotate — NEVER a deny/ask the encoder could turn into
        # a false "blocked" claim.
        self.assertEqual(applied.verb, Verb.ANNOTATE)
        self.assertNotIn(applied.verb, (Verb.DENY, Verb.ASK))
        # Exactly one signal, a dict, kind=capability_degraded, all four fields.
        self.assertIsInstance(signal, dict)
        self.assertEqual(signal["kind"], "capability_degraded")
        for f in _DEGRADE_FIELDS:
            self.assertIn(f, signal)
        self.assertEqual(signal["agent"], "gemini")
        self.assertEqual(signal["requested_verb"], "deny")
        self.assertEqual(signal["applied_verb"], "annotate")
        self.assertEqual(signal["reason"], "low CIR trust")

    def test_gemini_ask_downshifts_to_annotate(self):
        applied, signal = apply_capability(Decision.ask("needs operator"),
                                           "gemini", _ev("gemini"))
        self.assertEqual(applied.verb, Verb.ANNOTATE)
        self.assertEqual(signal["kind"], "capability_degraded")
        self.assertEqual(signal["requested_verb"], "ask")
        self.assertEqual(signal["applied_verb"], "annotate")

    def test_gemini_allow_and_pass_are_untouched(self):
        # Only deny/ask exceed Gemini's capability; allow/pass are honored as-is.
        for dec in (Decision.allow("fine"), Decision.pass_()):
            applied, signal = apply_capability(dec, "gemini", _ev("gemini"))
            self.assertIs(applied, dec)
            self.assertIsNone(signal)

    def test_gating_agents_keep_their_deny(self):
        # codex/cursor CAN gate (gate:True) — a deny passes through unchanged.
        for agent in ("codex", "cursor", "claude", "mentu"):
            applied, signal = apply_capability(Decision.deny("nope"), agent, _ev(agent))
            self.assertEqual(applied.verb, Verb.DENY)
            self.assertEqual(applied.reason, "nope")
            self.assertIsNone(signal)


class TestSupplyLadder(unittest.TestCase):
    """inject on a constrained / absent channel."""

    def test_partial_channel_inject_prepends_md_no_signal(self):
        dec = Decision.supply(md="## CIR brief\n- a fact")
        applied, signal = apply_capability(dec, "codex", _ev("codex", "prompt_submit"))
        self.assertEqual(applied.verb, Verb.INJECT)
        self.assertIsInstance(applied.updated_input, dict)
        self.assertIn("## CIR brief", applied.updated_input["prompt"])
        # A prompt prefix is a re-encoding, not a loss → no signal.
        self.assertIsNone(signal)

    def test_partial_channel_inject_keeps_existing_prompt(self):
        # The Agent-enrichment shape already lives in updated_input.prompt.
        dec = Decision.supply(prompt="enriched sub-agent prompt")
        applied, signal = apply_capability(dec, "cursor", _ev("cursor", "prompt_submit"))
        self.assertEqual(applied.verb, Verb.INJECT)
        self.assertEqual(applied.updated_input["prompt"], "enriched sub-agent prompt")
        self.assertIsNone(signal)

    def test_partial_channel_inject_prepends_md_before_existing_prompt(self):
        dec = Decision(verb=Verb.INJECT, inject_context="MD",
                       updated_input={"prompt": "ORIGINAL"})
        applied, _signal = apply_capability(dec, "gemini", _ev("gemini", "prompt_submit"))
        self.assertEqual(applied.updated_input["prompt"], "MD\n\nORIGINAL")

    def test_no_channel_inject_skips_with_supply_skipped(self):
        # No production agent has supply_context falsy, so synthesize one.
        nochan = {"observe": True, "supply_context": False,
                  "gate": True, "compaction": False}
        with mock.patch.dict(capabilities.CAPABILITIES, {"nochan": nochan}):
            applied, signal = apply_capability(Decision.supply(md="X"),
                                               "nochan", _ev("nochan", "prompt_submit"))
        # Skip: the decision degrades to pass (nothing supplied).
        self.assertEqual(applied.verb, Verb.PASS)
        self.assertIsInstance(signal, dict)
        self.assertEqual(signal["kind"], "supply_skipped")
        for f in _DEGRADE_FIELDS:
            self.assertIn(f, signal)
        self.assertEqual(signal["agent"], "nochan")
        self.assertEqual(signal["requested_verb"], "inject")
        self.assertEqual(signal["applied_verb"], "skip")

    def test_partial_channel_with_nothing_to_prepend_skips(self):
        # A partial channel but an empty inject payload has nothing to deliver →
        # skip + supply_skipped (never a phantom empty prompt).
        applied, signal = apply_capability(Decision.supply(md=""),
                                           "gemini", _ev("gemini", "prompt_submit"))
        self.assertEqual(applied.verb, Verb.PASS)
        self.assertEqual(signal["kind"], "supply_skipped")

    def test_full_channel_inject_passes_through(self):
        # claude / mentu have supply_context:True → keep the structured field.
        for agent in ("claude", "mentu"):
            dec = Decision.supply(md="brief")
            applied, signal = apply_capability(dec, agent, _ev(agent, "session_start"))
            self.assertIs(applied, dec)
            self.assertEqual(applied.inject_context, "brief")
            self.assertIsNone(signal)


class TestPassthroughAndFailOpen(unittest.TestCase):
    """Full-capability agents emit nothing; unknown agents never raise."""

    def test_full_capability_passthrough_emits_nothing(self):
        cases = [
            ("claude", Decision.deny("x")),
            ("claude", Decision.ask("x")),
            ("mentu", Decision.deny("x")),
            ("mentu", Decision.allow()),
            ("claude", Decision.pass_()),
        ]
        for agent, dec in cases:
            applied, signal = apply_capability(dec, agent, _ev(agent))
            self.assertIs(applied, dec, "%s should pass %s through unchanged"
                          % (agent, dec.verb))
            self.assertIsNone(signal)

    def test_unknown_agent_never_raises_and_passes_through(self):
        for dec in (Decision.deny("x"), Decision.ask("x"), Decision.supply(md="X"),
                    Decision.allow(), Decision.pass_()):
            applied, signal = apply_capability(dec, "frobnicate", _ev("frobnicate"))
            self.assertIs(applied, dec)
            self.assertIsNone(signal)

    def test_non_decision_input_fails_open(self):
        applied, signal = apply_capability(None, "claude", _ev("claude"))
        self.assertIsNone(applied)
        self.assertIsNone(signal)
        applied, signal = apply_capability("not a decision", "gemini", _ev("gemini"))
        self.assertEqual(applied, "not a decision")
        self.assertIsNone(signal)


if __name__ == "__main__":
    unittest.main()
