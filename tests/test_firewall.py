#!/usr/bin/env python3
"""Tests for the mentu safety firewall PreToolUse hook (hooks/pre-tool-use-firewall.py).

Builds a real throwaway git repo + scratch dirs and asserts that catastrophic
repo-destroyers exit 2 (blocked) while benign/scratch commands exit 0 (allowed).
Discoverable via `python3 -m unittest discover`; also runnable directly
(`python3 tests/test_firewall.py`). Never reads or writes your real home — every
path lives under a tempfile.mkdtemp() root.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HOOK = os.path.join(os.path.dirname(__file__), "..", "hooks", "pre-tool-use-firewall.py")
BLOCK, ALLOW = 2, 0


def _run(tool, tool_input, cwd):
    p = subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps({"tool_name": tool, "tool_input": tool_input, "cwd": cwd}),
        capture_output=True, text=True,
    )
    return p.returncode


class FirewallTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="fwtest-")
        cls.repo = os.path.join(cls.tmp, "repo")
        os.makedirs(cls.repo)
        subprocess.run(["git", "init", "-q"], cwd=cls.repo, check=True)
        cls.scratch = os.path.join(cls.repo, ".build")
        os.makedirs(cls.scratch)
        cls.nm = os.path.join(cls.repo, "node_modules")
        os.makedirs(cls.nm)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _bash(self, cmd, cwd):
        return _run("Bash", {"command": cmd}, cwd)

    def test_blocks_repo_destroyers(self):
        repo, tmp = self.repo, self.tmp
        cases = [
            ("rm -rf " + repo, tmp, "rm -rf <repo root>"),
            ("rm -rf .", repo, "rm -rf . at a repo root"),
            ("rm -rf .git", repo, "rm -rf .git"),
            ("rm -rf " + os.path.join(repo, ".git"), tmp, "rm -rf <repo>/.git"),
            ("rm -rf ~", tmp, "rm -rf ~"),
            ("rm -rf /", tmp, "rm -rf /"),
            ("cd /tmp && rm -rf " + repo, tmp, "compound && rm -rf <repo>"),
            ("git -C " + repo + " clean -fdx", tmp, "git clean -fdx in a repo"),
            ("find " + repo + " -delete", tmp, "find <repo> -delete"),
        ]
        for cmd, cwd, desc in cases:
            with self.subTest(desc=desc):
                self.assertEqual(self._bash(cmd, cwd), BLOCK, desc)

    def test_allows_scratch_and_benign(self):
        repo, tmp = self.repo, self.tmp
        cases = [
            ("find /tmp/some-logs-xyz -name '*.log' -delete", tmp, "find -delete on a non-repo"),
            ("rm -rf " + self.scratch, repo, "rm -rf .build (scratch)"),
            ("rm -rf " + self.nm, repo, "rm -rf node_modules"),
            ("rm -rf /tmp/whatever-xyz-123", tmp, "rm -rf a /tmp path"),
            ("rm file.txt", repo, "rm a single file (non-recursive)"),
            ("ls -la && echo hi", repo, "benign compound"),
            ("git clean -fd", repo, "git clean -fd (no -x) — untracked only"),
            ("swift build -c release", repo, "a build command"),
        ]
        for cmd, cwd, desc in cases:
            with self.subTest(desc=desc):
                self.assertEqual(self._bash(cmd, cwd), ALLOW, desc)

    def test_git_dir_write_guard(self):
        repo = self.repo
        block = [
            (os.path.join(repo, ".git", "config"), "Write <repo>/.git/config"),
            (os.path.join(repo, ".git", "hooks", "pre-commit"), "Write <repo>/.git/hooks/*"),
        ]
        allow = [
            (os.path.join(repo, "src", "file.swift"), "Write a normal source file"),
            (os.path.join(repo, ".gitignore"), "Write .gitignore (NOT .git)"),
        ]
        for fp, desc in block:
            with self.subTest(desc=desc):
                self.assertEqual(_run("Write", {"file_path": fp}, repo), BLOCK, desc)
        for fp, desc in allow:
            with self.subTest(desc=desc):
                self.assertEqual(_run("Write", {"file_path": fp}, repo), ALLOW, desc)


if __name__ == "__main__":
    unittest.main()
