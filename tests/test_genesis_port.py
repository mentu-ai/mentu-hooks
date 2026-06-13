#!/usr/bin/env python3
"""Tests for mentu_policy.genesis — the verbatim governance port (M2b).

Two layers:

  1. The five legacy federator tests (from hooks/test_genesis_reader.py),
     re-run against ``mentu_policy.genesis.GenesisReader`` — proving the
     federator hard-deny, the ``contribute`` op, and wildcard role resolution
     survived the port.
  2. A cross-implementation agreement test that imports BOTH the legacy
     ``hooks/genesis_reader.py`` AND ``mentu_policy.genesis`` and asserts they
     return identical ``resolve_role`` / ``actor_allowed`` verdicts across the
     federator matrix, the ungoverned default, and corrupt JSON. Any drift
     between the two implementations fails here.

Genesis configs are written into ``tempfile`` workspace dirs (passed as
``workspace_dir``); no real home is touched. Stdout from the legacy reader's
corrupt-JSON warning is captured so the suite output stays clean.
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from mentu_policy.genesis import GenesisReader as PortedGenesis

# Import the legacy reader (read-only) for the cross-implementation check.
_HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))
import genesis_reader as legacy_genesis  # noqa: E402  (hooks/genesis_reader.py)

LegacyGenesis = legacy_genesis.GenesisReader


# ---------------------------------------------------------------------------
# Fixtures (mirrors hooks/test_genesis_reader.py)
# ---------------------------------------------------------------------------

def _write_genesis(tmpdir: str, config: dict) -> str:
    mentu_dir = os.path.join(tmpdir, ".mentu")
    os.makedirs(mentu_dir, exist_ok=True)
    path = os.path.join(mentu_dir, "genesis.json")
    with open(path, "w") as f:
        json.dump(config, f)
    return tmpdir


def _write_raw_genesis(tmpdir: str, raw: str) -> str:
    mentu_dir = os.path.join(tmpdir, ".mentu")
    os.makedirs(mentu_dir, exist_ok=True)
    with open(os.path.join(mentu_dir, "genesis.json"), "w") as f:
        f.write(raw)
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


def _quiet():
    """Swallow the legacy reader's corrupt-JSON warning print to stdout."""
    return contextlib.redirect_stdout(io.StringIO())


# ---------------------------------------------------------------------------
# 1. The five legacy federator tests, re-run against the ported reader
# ---------------------------------------------------------------------------

class TestFederatorRolePorted(unittest.TestCase):

    def test_human_federator_allowed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_genesis(tmpdir, _federator_config())
            reader = PortedGenesis(tmpdir)
            self.assertEqual(reader.resolve_role("human:bob"), "federator")
            self.assertTrue(reader.actor_allowed("human:bob", "contribute"))
            self.assertTrue(reader.actor_allowed("human:bob", "capture"))

    def test_seeder_federator_allowed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_genesis(tmpdir, _federator_config())
            reader = PortedGenesis(tmpdir)
            self.assertEqual(reader.resolve_role("seeder:relay-1"), "federator")
            self.assertTrue(reader.actor_allowed("seeder:relay-1", "contribute"))

    def test_agent_federator_denied(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_genesis(tmpdir, _federator_config())
            reader = PortedGenesis(tmpdir)
            # Resolves to federator by wildcard, but the agent:* hard-deny fires.
            self.assertEqual(reader.resolve_role("agent:claude"), "federator")
            self.assertFalse(reader.actor_allowed("agent:claude", "contribute"))

    def test_agent_federator_denied_all_ops(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_genesis(tmpdir, _federator_config())
            reader = PortedGenesis(tmpdir)
            for op in ["capture", "commit", "claim", "release", "annotate", "submit", "contribute"]:
                self.assertFalse(
                    reader.actor_allowed("agent:any-agent", op),
                    f"agent:any-agent should be denied '{op}' when resolved to federator",
                )

    def test_federator_in_permissions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_genesis(tmpdir, _federator_config())
            reader = PortedGenesis(tmpdir)
            perms = reader.config.get("permissions", {})
            self.assertIn("federator", perms)
            self.assertIn("contribute", perms["federator"])


# ---------------------------------------------------------------------------
# 2. Cross-implementation agreement: legacy vs ported, identical verdicts
# ---------------------------------------------------------------------------

class TestCrossImplementationAgreement(unittest.TestCase):

    # The full op vocabulary the readers reason over.
    OPS = ["capture", "commit", "claim", "release", "close",
           "annotate", "submit", "approve", "reopen", "contribute"]

    # A matrix spanning every actor-matching branch: exact, agent:* (hard-deny),
    # human:* wildcard, seeder exact, admin, unknown, and the bare "*" actor.
    ACTORS = [
        "human:alice", "human:bob", "human:carol",
        "seeder:relay-1",
        "agent:claude", "agent:codex", "agent:whatever",
        "reviewer:r1", "nobody:nobody", "*", "user", "",
    ]

    def _assert_agree(self, legacy, ported):
        for actor in self.ACTORS:
            self.assertEqual(
                legacy.resolve_role(actor), ported.resolve_role(actor),
                f"resolve_role disagreement for {actor!r}")
            for op in self.OPS:
                self.assertEqual(
                    legacy.actor_allowed(actor, op),
                    ported.actor_allowed(actor, op),
                    f"actor_allowed disagreement for {actor!r}/{op!r}")
            self.assertEqual(
                legacy.get_allowed_ops(actor), ported.get_allowed_ops(actor),
                f"get_allowed_ops disagreement for {actor!r}")
            self.assertEqual(
                legacy.get_denied_ops(actor), ported.get_denied_ops(actor),
                f"get_denied_ops disagreement for {actor!r}")

    def test_federator_matrix_agrees(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_genesis(tmpdir, _federator_config())
            self._assert_agree(LegacyGenesis(tmpdir), PortedGenesis(tmpdir))

    def test_ungoverned_default_agrees(self):
        # No genesis.json => both permissive: actor_allowed True, ops ["*"].
        with tempfile.TemporaryDirectory() as tmpdir:
            legacy, ported = LegacyGenesis(tmpdir), PortedGenesis(tmpdir)
            self.assertFalse(legacy.governed)
            self.assertFalse(ported.governed)
            self._assert_agree(legacy, ported)
            self.assertTrue(ported.actor_allowed("agent:x", "capture"))
            self.assertEqual(ported.get_allowed_ops("agent:x"), ["*"])
            self.assertEqual(ported.scope, ["*"])  # inert, preserved

    def test_corrupt_json_agrees_and_falls_back_ungoverned(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_raw_genesis(tmpdir, "{ this is not valid json ]]")
            with _quiet():
                legacy = LegacyGenesis(tmpdir)
                ported = PortedGenesis(tmpdir)
            self.assertFalse(legacy.governed)
            self.assertFalse(ported.governed)
            self._assert_agree(legacy, ported)

    def test_non_object_json_agrees_and_falls_back_ungoverned(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_raw_genesis(tmpdir, "[1, 2, 3]")
            with _quiet():
                legacy = LegacyGenesis(tmpdir)
                ported = PortedGenesis(tmpdir)
            self.assertFalse(legacy.governed)
            self.assertFalse(ported.governed)
            self._assert_agree(legacy, ported)


if __name__ == "__main__":
    unittest.main()
