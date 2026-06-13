#!/usr/bin/env python3
"""Tests for genesis_reader.py — federator role + agent:* deny."""

import json
import os
import tempfile
import unittest

from genesis_reader import GenesisReader


def _write_genesis(tmpdir: str, config: dict) -> str:
    mentu_dir = os.path.join(tmpdir, ".mentu")
    os.makedirs(mentu_dir, exist_ok=True)
    path = os.path.join(mentu_dir, "genesis.json")
    with open(path, "w") as f:
        json.dump(config, f)
    return tmpdir


def _federator_config() -> dict:
    return {
        "identity": {"name": "test", "owner": "human:alice", "created": "2026-04-21"},
        "actors": [
            {"id": "human:alice", "role": "admin"},
            {"id": "human:bob", "role": "federator"},
            {"id": "seeder:relay-1", "role": "federator"},
            {"id": "agent:*", "role": "federator"},
            {"id": "human:*", "role": "contributor"},
        ],
        "permissions": {
            "admin": ["*"],
            "federator": ["capture", "commit", "claim", "release", "annotate", "submit", "contribute"],
            "contributor": ["capture", "commit", "claim", "release", "close", "annotate", "submit"],
            "agent": ["capture", "commit", "claim", "release", "submit", "annotate"],
            "reviewer": ["approve", "reopen"],
        },
    }


class TestFederatorRole(unittest.TestCase):

    def test_human_federator_allowed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_genesis(tmpdir, _federator_config())
            reader = GenesisReader(tmpdir)
            self.assertEqual(reader.resolve_role("human:bob"), "federator")
            self.assertTrue(reader.actor_allowed("human:bob", "contribute"))
            self.assertTrue(reader.actor_allowed("human:bob", "capture"))

    def test_seeder_federator_allowed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_genesis(tmpdir, _federator_config())
            reader = GenesisReader(tmpdir)
            self.assertEqual(reader.resolve_role("seeder:relay-1"), "federator")
            self.assertTrue(reader.actor_allowed("seeder:relay-1", "contribute"))

    def test_agent_federator_denied(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_genesis(tmpdir, _federator_config())
            reader = GenesisReader(tmpdir)
            self.assertEqual(reader.resolve_role("agent:claude"), "federator")
            self.assertFalse(reader.actor_allowed("agent:claude", "contribute"))

    def test_agent_federator_denied_all_ops(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_genesis(tmpdir, _federator_config())
            reader = GenesisReader(tmpdir)
            for op in ["capture", "commit", "claim", "release", "annotate", "submit", "contribute"]:
                self.assertFalse(
                    reader.actor_allowed("agent:any-agent", op),
                    f"agent:any-agent should be denied '{op}' when resolved to federator",
                )

    def test_federator_in_permissions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_genesis(tmpdir, _federator_config())
            reader = GenesisReader(tmpdir)
            perms = reader.config.get("permissions", {})
            self.assertIn("federator", perms)
            self.assertIn("contribute", perms["federator"])


if __name__ == "__main__":
    unittest.main()
