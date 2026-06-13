#!/usr/bin/env python3
"""Tests for mentu_policy.capabilities — the per-agent enforcement registry."""

import unittest

from mentu_policy.capabilities import CAPABILITIES, supports

TIERS = ("observe", "supply_context", "gate", "compaction")


class TestMatrix(unittest.TestCase):
    def test_exactly_five_agents(self):
        self.assertEqual(set(CAPABILITIES), {"claude", "codex", "cursor", "gemini", "mentu"})

    def test_rows_match_spec_table(self):
        self.assertEqual(
            CAPABILITIES["claude"],
            {"observe": True, "supply_context": True, "gate": True, "compaction": True},
        )
        self.assertEqual(
            CAPABILITIES["codex"],
            {"observe": True, "supply_context": "partial", "gate": True, "compaction": False},
        )
        self.assertEqual(
            CAPABILITIES["cursor"],
            {"observe": True, "supply_context": "partial", "gate": True, "compaction": False},
        )
        self.assertEqual(
            CAPABILITIES["gemini"],
            {"observe": True, "supply_context": "partial", "gate": False, "compaction": False},
        )
        self.assertEqual(
            CAPABILITIES["mentu"],
            {"observe": True, "supply_context": True, "gate": True, "compaction": True},
        )

    def test_all_true_rows_are_exactly_claude_and_mentu(self):
        all_true = {
            agent
            for agent, caps in CAPABILITIES.items()
            if all(caps.get(t) is True for t in TIERS)
        }
        self.assertEqual(all_true, {"claude", "mentu"})


class TestSupports(unittest.TestCase):
    def test_partial_is_not_true(self):
        self.assertFalse(supports("codex", "supply_context"))
        self.assertFalse(supports("cursor", "supply_context"))
        self.assertFalse(supports("gemini", "supply_context"))

    def test_native_channels_are_true(self):
        self.assertTrue(supports("claude", "supply_context"))
        self.assertTrue(supports("mentu", "compaction"))
        self.assertTrue(supports("codex", "gate"))
        for agent in CAPABILITIES:
            self.assertTrue(supports(agent, "observe"))

    def test_gemini_cannot_gate(self):
        self.assertFalse(supports("gemini", "gate"))

    def test_unknown_agent_is_false(self):
        for tier in TIERS:
            self.assertFalse(supports("unknown", tier))
        self.assertFalse(supports("", "observe"))
        self.assertFalse(supports("copilot", "gate"))

    def test_unknown_tier_is_false(self):
        self.assertFalse(supports("claude", "teleport"))
        self.assertFalse(supports("mentu", ""))


if __name__ == "__main__":
    unittest.main()
