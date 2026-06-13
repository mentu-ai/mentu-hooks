#!/usr/bin/env python3
"""Tests for scripts/install-agent-hooks.sh — the capability-aware installer.

EVERY test runs the installer against a throwaway ``HOME=$(mktemp -d)`` — the
real home is NEVER touched. Asserts the M4 contract: self-contained package +
shim deploy, idempotent re-runs, per-config backups, Gemini observe-only (no
pre-action gate), and the only-touch-existing-config-dir guard."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent          # mentu-hooks/
INSTALLER = REPO / "scripts" / "install-agent-hooks.sh"

_CONFIGS = (
    ".claude/settings.json",
    ".cursor/hooks.json",
    ".gemini/settings.json",
    ".codex/hooks.json",
)


class InstallerTestBase(unittest.TestCase):
    def setUp(self):
        # A throwaway HOME — asserted against, never the operator's real home.
        self.home = Path(tempfile.mkdtemp(prefix="mentu-inst-home-"))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)

    def run_installer(self):
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        # Never inherit a MENTU_HOME / actor env from the test runner.
        for k in ("MENTU_HOME", "MENTU_ACTOR", "CURSOR_SESSION_ID",
                  "CODEX_SESSION_ID", "GEMINI_SESSION_ID", "SUPERSET_TAB_ID"):
            env.pop(k, None)
        r = subprocess.run(["bash", str(INSTALLER)], env=env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0,
                         "installer exited %d\nstdout:\n%s\nstderr:\n%s"
                         % (r.returncode, r.stdout, r.stderr))
        return r

    def seed_all(self):
        (self.home / ".claude").mkdir(parents=True)
        (self.home / ".claude" / "settings.json").write_text(
            '{"model": "opus", "hooks": {}}\n')
        for d in (".cursor", ".gemini", ".codex"):
            (self.home / d).mkdir(parents=True)

    def config_bytes(self):
        out = {}
        for rel in _CONFIGS:
            p = self.home / rel
            out[rel] = p.read_bytes() if p.exists() else None
        return out


class TestDeployAndIdempotency(InstallerTestBase):
    def test_two_runs_produce_identical_configs(self):
        self.seed_all()
        self.run_installer()
        snap1 = self.config_bytes()
        self.run_installer()
        snap2 = self.config_bytes()
        self.assertEqual(snap1, snap2,
                         "configs changed on the second run (non-idempotent)")

    def test_no_duplicate_hook_entries_on_rerun(self):
        self.seed_all()
        self.run_installer()
        self.run_installer()
        claude = json.loads((self.home / ".claude" / "settings.json").read_text())
        for event, lst in claude["hooks"].items():
            cmds = [h["command"] for e in lst for h in e.get("hooks", [])]
            self.assertEqual(len(cmds), len(set(cmds)),
                             "duplicate hook commands in %s after re-run: %s"
                             % (event, cmds))

    def test_package_and_shims_deployed_self_contained(self):
        self.seed_all()
        self.run_installer()
        hooks = self.home / ".mentu" / "hooks"
        # the package travels with the shims so a deployed copy is self-contained
        self.assertTrue((hooks / "mentu_policy" / "abi.py").exists(),
                        "mentu_policy/abi.py not deployed")
        self.assertTrue((hooks / "mentu_policy" / "adapters" / "shim.py").exists())
        for shim in ("mentu_agent_hook.sh", "codex_cir_hook.sh",
                     "cursor_cir_hook.sh", "gemini_cir_hook.sh",
                     "pre-tool-use-permission.sh", "review_gate.py",
                     "context_isolation_gate.py"):
            self.assertTrue((hooks / shim).exists(), "shim not deployed: %s" % shim)

    def test_backups_exist(self):
        self.seed_all()
        self.run_installer()
        backups_root = self.home / ".mentu" / "backups"
        dirs = [p for p in backups_root.iterdir() if p.is_dir()] \
            if backups_root.exists() else []
        self.assertTrue(dirs, "no backup directory created")
        self.assertTrue(any((d / "claude-settings.json").exists() for d in dirs),
                        "seeded Claude settings was not backed up before editing")


class TestCapabilityAwareWiring(InstallerTestBase):
    def test_gemini_config_has_no_gate_or_permission_wiring(self):
        self.seed_all()
        self.run_installer()
        text = (self.home / ".gemini" / "settings.json").read_text()
        gemini = json.loads(text)
        # Observe-only: exactly the three post-hoc lifecycle events.
        self.assertEqual(set(gemini["hooks"]),
                         {"BeforeAgent", "AfterAgent", "AfterTool"})
        # No pre-action gate surface may appear ANYWHERE in the gemini config.
        for needle in ("PreToolUse", "PermissionRequest", "beforeShellExecution",
                       "beforeMCPExecution", "permissionDecision", "permission",
                       "_approval_request"):
            self.assertNotIn(needle, text,
                             "gemini config leaked gate wiring: %r" % needle)

    def test_claude_and_cursor_get_gate_wiring(self):
        self.seed_all()
        self.run_installer()
        claude = json.loads((self.home / ".claude" / "settings.json").read_text())
        # Claude (gate:True) gets the pre-action gates.
        self.assertIn("PreToolUse", claude["hooks"])
        self.assertIn("Stop", claude["hooks"])
        self.assertIn("SubagentStop", claude["hooks"])
        cursor = json.loads((self.home / ".cursor" / "hooks.json").read_text())
        # Cursor (gate:True) gets the pre-action shell/MCP gates.
        self.assertIn("beforeShellExecution", cursor)
        self.assertIn("beforeMCPExecution", cursor)


class TestExistingConfigDirGuard(InstallerTestBase):
    def test_absent_cursor_creates_no_cursor_config(self):
        # Seed everything EXCEPT ~/.cursor.
        (self.home / ".claude").mkdir(parents=True)
        (self.home / ".claude" / "settings.json").write_text("{}\n")
        (self.home / ".gemini").mkdir(parents=True)
        (self.home / ".codex").mkdir(parents=True)
        self.run_installer()
        self.assertFalse((self.home / ".cursor").exists(),
                         "installer created ~/.cursor when it did not exist")
        self.assertFalse((self.home / ".cursor" / "hooks.json").exists())
        # the other agents were still wired
        self.assertTrue((self.home / ".gemini" / "settings.json").exists())
        self.assertTrue((self.home / ".codex" / "hooks.json").exists())


if __name__ == "__main__":
    unittest.main()
