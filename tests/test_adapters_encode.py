#!/usr/bin/env python3
"""Encoder exhaustiveness: every adapter's ``encode`` returns a native response
for EVERY ``Verb`` on EVERY surface it serves — a missing case is a test
failure, never a silent drop (BUILD §M1 acceptance).

Exhaustive over Verb × {claude, codex, cursor, gemini}."""

import unittest

from mentu_policy.abi import AgentEvent, Decision, ToolRef, Verb
from mentu_policy.adapters import claude, codex, cursor, gemini


def _decision(verb):
    """A representative Decision for each verb (payload-bearing where the verb
    carries one), so the encoders exercise their real rendering path."""
    if verb == Verb.INJECT:
        return Decision(verb=Verb.INJECT, inject_context="## brief",
                        updated_input={"prompt": "enriched"})
    if verb == Verb.ANNOTATE:
        return Decision(verb=Verb.ANNOTATE, annotate={"kind": "k", "body": "b"})
    if verb in (Verb.DENY, Verb.ASK):
        return Decision(verb=verb, reason="because")
    return Decision(verb=verb, reason="ok")


_CLAUDE_SURFACES = [
    AgentEvent(agent="claude", event="stop", message="m"),
    AgentEvent(agent="claude", event="subagent_stop", message="m"),
    AgentEvent(agent="claude", event="pre_tool",
               tool=ToolRef(name="Bash", input={"command": "ls"})),
    AgentEvent(agent="claude", event="permission_request",
               tool=ToolRef(name="Bash", input={})),
    AgentEvent(agent="claude", event="session_start"),
    AgentEvent(agent="claude", event="post_compact", source="compact"),
]


class TestClaudeEncodeExhaustive(unittest.TestCase):
    def test_every_verb_on_every_surface_returns_a_response(self):
        for event in _CLAUDE_SURFACES:
            for verb in Verb:
                out, code = claude.encode(_decision(verb), event)
                self.assertIsInstance(out, str,
                                      "claude.encode(%s, %s) stdout not str"
                                      % (verb, event.event))
                self.assertIsInstance(code, int,
                                      "claude.encode(%s, %s) exit not int"
                                      % (verb, event.event))
                self.assertIn(code, (0, 2))

    def test_stop_deny_is_exit_2_with_block_text(self):
        ev = AgentEvent(agent="claude", event="stop")
        out, code = claude.encode(Decision.deny("blocked because X"), ev)
        self.assertEqual(code, 2)
        self.assertEqual(out, "blocked because X\n")

    def test_subagent_stop_allow_is_silent_exit_0(self):
        ev = AgentEvent(agent="claude", event="subagent_stop")
        out, code = claude.encode(Decision.allow(), ev)
        self.assertEqual((out, code), ("", 0))

    def test_permission_allow_deny_pass_shapes(self):
        ev = AgentEvent(agent="claude", event="pre_tool",
                        tool=ToolRef(name="Bash", input={}))
        allow, ca = claude.encode(Decision.allow("trust high"), ev)
        self.assertEqual(ca, 0)
        self.assertIn('"permissionDecision": "allow"', allow)
        self.assertIn('"permissionDecisionReason": "trust high"', allow)
        self.assertTrue(allow.endswith("}\n"))
        deny, _ = claude.encode(Decision.deny("trust low"), ev)
        self.assertIn('"permissionDecision": "deny"', deny)
        passout, _ = claude.encode(Decision.pass_(), ev)
        self.assertEqual(passout, "{}\n")

    def test_inject_updated_input_shape_is_utf8_literal(self):
        ev = AgentEvent(agent="claude", event="pre_tool",
                        tool=ToolRef(name="Agent", input={}))
        d = Decision.supply(prompt="orig — dashed")
        out, code = claude.encode(d, ev)
        self.assertEqual(code, 0)
        self.assertIn('"updatedInput"', out)
        self.assertIn("orig — dashed", out)        # literal em dash, not —
        self.assertNotIn("\\u2014", out)

    def test_session_start_inject_is_raw_markdown(self):
        ev = AgentEvent(agent="claude", event="session_start")
        out, code = claude.encode(Decision.supply(md="## CIR\n\n- x"), ev)
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("## CIR"))
        # session_start restores the structural trailing newline (ends \n\n).
        self.assertTrue(out.endswith("\n\n"))

    def test_post_compact_inject_single_trailing_newline_added(self):
        ev = AgentEvent(agent="claude", event="post_compact", source="compact")
        out, code = claude.encode(Decision.supply(md="## State\n"), ev)
        self.assertEqual(out, "## State\n\n")


# ---------------------------------------------------------------------------
# Codex / Cursor / Gemini encoders — exhaustive over Verb (Commit C)
# ---------------------------------------------------------------------------

class TestAgentEncodeExhaustive(unittest.TestCase):
    _SURFACES = {
        codex: [AgentEvent(agent="codex", event="permission_request"),
                AgentEvent(agent="codex", event="pre_tool"),
                AgentEvent(agent="codex", event="post_tool")],
        cursor: [AgentEvent(agent="cursor", event="permission_request"),
                 AgentEvent(agent="cursor", event="prompt_submit"),
                 AgentEvent(agent="cursor", event="stop")],
        gemini: [AgentEvent(agent="gemini", event="prompt_submit"),
                 AgentEvent(agent="gemini", event="post_tool"),
                 AgentEvent(agent="gemini", event="stop")],
    }

    def test_every_verb_every_agent_returns_a_response(self):
        for adapter, surfaces in self._SURFACES.items():
            for event in surfaces:
                for verb in Verb:
                    out, code = adapter.encode(_decision(verb), event)
                    self.assertIsInstance(out, str,
                                          "%s.encode(%s, %s) stdout not str"
                                          % (adapter.AGENT, verb, event.event))
                    self.assertIsInstance(code, int)
                    self.assertEqual(code, 0, "agent encoders never exit non-zero")


class TestCursorVerdictWiring(unittest.TestCase):
    """The cursor behavior change: the verdict drives the response, replacing the
    legacy hardcoded {"continue":true}."""

    def _perm(self):
        return AgentEvent(agent="cursor", event="permission_request")

    def test_pass_is_continue_true_no_space(self):
        # byte-exact to the legacy echo (no space after the colon).
        out, _ = cursor.encode(Decision.pass_(), self._perm())
        self.assertEqual(out, '{"continue":true}\n')

    def test_allow_is_continue_true(self):
        out, _ = cursor.encode(Decision.allow("trust high"), self._perm())
        self.assertEqual(out, '{"continue":true}\n')

    def test_deny_is_continue_false_with_reason(self):
        out, _ = cursor.encode(Decision.deny("trust below threshold"), self._perm())
        self.assertEqual(out, '{"continue":false,"reason":"trust below threshold"}\n')

    def test_ask_is_continue_false(self):
        out, _ = cursor.encode(Decision.ask("needs human"), self._perm())
        self.assertIn('"continue":false', out)

    def test_non_permission_event_is_empty_object(self):
        out, _ = cursor.encode(Decision.deny("x"),
                               AgentEvent(agent="cursor", event="stop"))
        self.assertEqual(out, "{}\n")


class TestGeminiNeverBlocks(unittest.TestCase):
    """Gemini is gate-incapable (post-hoc); its encoder NEVER emits a block
    shape — every verb degrades to {} (the shim logs capability_degraded)."""

    def test_all_verbs_are_empty_object(self):
        ev = AgentEvent(agent="gemini", event="post_tool")
        for verb in Verb:
            out, code = gemini.encode(_decision(verb), ev)
            self.assertEqual((out, code), ("{}\n", 0),
                             "gemini.encode(%s) must be {} (no block shape)" % verb)


class TestCodexApprovalShapes(unittest.TestCase):
    def test_allow_is_approve(self):
        ev = AgentEvent(agent="codex", event="permission_request")
        out, _ = codex.encode(Decision.allow("ok"), ev)
        self.assertEqual(out, '{"decision":"approve"}\n')

    def test_deny_is_deny_with_reason(self):
        ev = AgentEvent(agent="codex", event="permission_request")
        out, _ = codex.encode(Decision.deny("nope"), ev)
        self.assertIn('"decision":"deny"', out)
        self.assertIn("nope", out)

    def test_pass_is_empty_object(self):
        ev = AgentEvent(agent="codex", event="post_tool")
        out, _ = codex.encode(Decision.pass_(), ev)
        self.assertEqual(out, "{}\n")


if __name__ == "__main__":
    unittest.main()
