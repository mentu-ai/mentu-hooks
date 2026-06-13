#!/usr/bin/env python3
"""Tests for mentu_policy.adapters.io.resolve_actor — the actor-precedence
matrix ported verbatim from hooks/mentu_agent_hook.sh:32-50.

Hermetic: every case passes an explicit ``environ`` dict; the process
environment never leaks in (the precedence must be driven only by the case)."""

import unittest

from mentu_policy.adapters import io


class TestResolveActorPrecedence(unittest.TestCase):
    def test_default_is_agent_claude(self):
        self.assertEqual(io.resolve_actor({}), "agent:claude")

    def test_mentu_actor_override_wins(self):
        env = {"MENTU_ACTOR": "human:rashid", "CURSOR_SESSION_ID": "cs-1",
               "CODEX_SESSION_ID": "cx-1"}
        self.assertEqual(io.resolve_actor(env), "human:rashid")

    def test_superset_before_cursor(self):
        env = {"SUPERSET_TAB_ID": "ss-1", "CURSOR_SESSION_ID": "cs-1"}
        self.assertEqual(io.resolve_actor(env), "agent:superset-hosted")

    def test_cursor_session(self):
        self.assertEqual(io.resolve_actor({"CURSOR_SESSION_ID": "cs-1"}), "agent:cursor")

    def test_codex_after_cursor(self):
        # cursor outranks codex when both present
        env = {"CURSOR_SESSION_ID": "cs-1", "CODEX_SESSION_ID": "cx-1"}
        self.assertEqual(io.resolve_actor(env), "agent:cursor")
        self.assertEqual(io.resolve_actor({"CODEX_SESSION_ID": "cx-1"}), "agent:codex")

    def test_gemini_last_agent_var(self):
        env = {"CODEX_SESSION_ID": "cx-1", "GEMINI_SESSION_ID": "gm-1"}
        self.assertEqual(io.resolve_actor(env), "agent:codex")
        self.assertEqual(io.resolve_actor({"GEMINI_SESSION_ID": "gm-1"}), "agent:gemini")

    def test_full_priority_order(self):
        # Establish the entire chain by peeling one var at a time.
        base = {
            "MENTU_ACTOR": "agent:root",
            "SUPERSET_TAB_ID": "ss",
            "CURSOR_SESSION_ID": "cs",
            "CODEX_SESSION_ID": "cx",
            "GEMINI_SESSION_ID": "gm",
        }
        expected = ["agent:root", "agent:superset-hosted", "agent:cursor",
                    "agent:codex", "agent:gemini", "agent:claude"]
        keys = ["MENTU_ACTOR", "SUPERSET_TAB_ID", "CURSOR_SESSION_ID",
                "CODEX_SESSION_ID", "GEMINI_SESSION_ID"]
        env = dict(base)
        for i, exp in enumerate(expected):
            self.assertEqual(io.resolve_actor(env), exp)
            if i < len(keys):
                env.pop(keys[i])


class TestActorFormatValidation(unittest.TestCase):
    def test_no_colon_normalizes_to_agent_unknown(self):
        self.assertEqual(io.resolve_actor({"MENTU_ACTOR": "noColon"}), "agent:unknown")

    def test_colon_value_passes_through(self):
        self.assertEqual(io.resolve_actor({"MENTU_ACTOR": "human:rashid"}), "human:rashid")

    def test_empty_mentu_actor_is_skipped(self):
        # bash ``[[ -n "${MENTU_ACTOR:-}" ]]`` treats empty as unset.
        env = {"MENTU_ACTOR": "", "CURSOR_SESSION_ID": "cs-1"}
        self.assertEqual(io.resolve_actor(env), "agent:cursor")

    def test_empty_everything_defaults_claude(self):
        env = {"MENTU_ACTOR": "", "SUPERSET_TAB_ID": "", "CURSOR_SESSION_ID": "",
               "CODEX_SESSION_ID": "", "GEMINI_SESSION_ID": ""}
        self.assertEqual(io.resolve_actor(env), "agent:claude")


class TestParseJson(unittest.TestCase):
    def test_valid_object(self):
        self.assertEqual(io.parse_json('{"a": 1}'), {"a": 1})

    def test_garbage_is_empty_dict(self):
        self.assertEqual(io.parse_json("not json {{{"), {})

    def test_non_object_is_empty_dict(self):
        self.assertEqual(io.parse_json("[1, 2, 3]"), {})
        self.assertEqual(io.parse_json('"a string"'), {})

    def test_empty_is_empty_dict(self):
        self.assertEqual(io.parse_json(""), {})


if __name__ == "__main__":
    unittest.main()
