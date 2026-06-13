#!/usr/bin/env python3
"""Tests for the M2b supply + observe engines and the fail-open substrate facade.

Hermetic by construction — NO subprocess, NO real socket, NO real home:

  * the absent-substrate tests point ``MENTU_HOME`` at an empty mktemp and
    force the no-binary branch (``Substrate(mentu_bin=<nonexistent>)``), so
    ``available()`` is False and supply degrades to PASS without spawning the
    real ``mentu`` binary that happens to be on PATH;
  * the read-only proof injects a RecordingSubstrate fake (never the real
    facade) and asserts ``supply_context`` touched zero mutating methods;
  * the workspace reads are keyed off ``event.cwd`` / ``ctx["cwd"]`` pointed at
    mktemp dirs, never the process CWD.

Covers the M2b acceptance contract: fail-open => PASS; read-only supply path;
observe is structurally pass-or-annotate over a fuzz corpus.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mentu_policy import core
from mentu_policy.abi import AgentEvent, Decision, ToolRef, Verb
from mentu_policy.observe import classify_evidence, observe, observe_engine
from mentu_policy.substrate import Substrate, default_base_dir
from mentu_policy.supply import supply_context, supply_engine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def session_start(actor="agent:claude", cwd="/tmp/does-not-exist"):
    return AgentEvent(agent="claude", event="session_start", actor=actor, cwd=cwd)


def post_compact(cwd, source="compact"):
    return AgentEvent(agent="claude", event="post_compact", cwd=cwd, source=source)


def agent_pre_tool(prompt, cwd):
    return AgentEvent(agent="claude", event="pre_tool", cwd=cwd,
                      tool=ToolRef(name="Agent", input={"prompt": prompt}))


def post_tool(name, inp, output=None, exit_code=None, event="post_tool"):
    return AgentEvent(agent="claude", event=event,
                      tool=ToolRef(name=name, input=inp, output=output, exit_code=exit_code))


def absent_substrate(base):
    """A real facade pointed at an empty mktemp with a forced-absent binary —
    available() is False without touching the real home or PATH binary."""
    return Substrate(base_dir=base, mentu_bin=str(Path(base) / "nobin" / "mentu"))


# ---------------------------------------------------------------------------
# RecordingSubstrate — duck-typed fake that records every method touched.
# ---------------------------------------------------------------------------

class RecordingSubstrate:
    """Available, returns canned read data, and RECORDS every method call so
    the supply path can be proven side-effect-free. The mutating methods are
    present (so a stray call is caught), but each records-then-no-ops."""

    MUTATING = {"capture", "annotate", "sync", "cir_capture",
                "submit", "close", "claim", "commit"}

    def __init__(self, patterns=None, signals=None, claimed=None, socket=True):
        self._patterns = patterns
        self._signals = signals
        self._claimed = list(claimed or [])
        self._socket = socket
        self.calls = []

    def _rec(self, name):
        self.calls.append(name)

    # availability / reads
    def available(self):
        self._rec("available"); return True

    def socket_available(self):
        self._rec("socket_available"); return self._socket

    def cli_available(self):
        self._rec("cli_available"); return True

    def cir_patterns(self):
        self._rec("cir_patterns"); return self._patterns

    def cir_query(self, limit=5):
        self._rec("cir_query"); return self._signals

    def list_commitments(self, state=None, actor=None, limit=50):
        self._rec("list_commitments"); return list(self._claimed)

    def status(self, commitment_id):
        self._rec("status"); return None

    # mutations — recorded, then no-op (must never be hit on the supply path)
    def capture(self, *a, **k):
        self._rec("capture"); return None

    def annotate(self, *a, **k):
        self._rec("annotate"); return False

    def sync(self, *a, **k):
        self._rec("sync"); return None

    def cir_capture(self, *a, **k):
        self._rec("cir_capture"); return False

    def call(self, *a, **k):
        self._rec("call"); return None


# ---------------------------------------------------------------------------
# Fail-open: absent substrate => PASS (supply) and PASS (observe)
# ---------------------------------------------------------------------------

class TestFailOpenAbsentSubstrate(unittest.TestCase):
    def test_absent_facade_reports_unavailable(self):
        with tempfile.TemporaryDirectory() as base:
            sub = absent_substrate(base)
            self.assertFalse(sub.socket_available())
            self.assertFalse(sub.cli_available())
            self.assertFalse(sub.available())

    def test_supply_session_start_absent_passes(self):
        with tempfile.TemporaryDirectory() as base:
            sub = absent_substrate(base)
            d = supply_context(session_start(), {"substrate": sub})
            self.assertEqual(d.verb, Verb.PASS)

    def test_supply_session_start_no_ctx_passes(self):
        # ctx=None (how core.evaluate calls it) => no substrate => PASS.
        self.assertEqual(supply_context(session_start(), None).verb, Verb.PASS)
        self.assertEqual(supply_engine(session_start(), None).verb, Verb.PASS)

    def test_supply_session_start_no_substrate_key_passes(self):
        self.assertEqual(supply_context(session_start(), {}).verb, Verb.PASS)

    def test_supply_post_compact_absent_workspace_passes(self):
        with tempfile.TemporaryDirectory() as ws:
            # empty workspace: no .claude/pre-compact-state.json
            d = supply_context(post_compact(cwd=ws), {})
            self.assertEqual(d.verb, Verb.PASS)

    def test_supply_pre_tool_absent_ledger_passes(self):
        with tempfile.TemporaryDirectory() as ws:
            d = supply_context(agent_pre_tool("hello", cwd=ws), {})
            self.assertEqual(d.verb, Verb.PASS)

    def test_observe_non_evidence_event_passes(self):
        # observe is substrate-independent; a non-build Bash classifies to skip.
        d = observe(post_tool("Bash", {"command": "ls -la"}, exit_code=0))
        self.assertEqual(d.verb, Verb.PASS)


# ---------------------------------------------------------------------------
# Substrate base-dir resolution honors MENTU_HOME (never the real home)
# ---------------------------------------------------------------------------

class TestSubstrateBaseDir(unittest.TestCase):
    def test_mentu_home_redirects_base_dir(self):
        with tempfile.TemporaryDirectory() as home:
            with mock.patch.dict(os.environ, {"MENTU_HOME": home}):
                self.assertEqual(default_base_dir(), Path(home))
                sub = Substrate(mentu_bin=str(Path(home) / "nobin"))
                self.assertEqual(sub.base_dir, Path(home))
                self.assertEqual(sub.socket_path, Path(home) / "mentu-local.sock")

    def test_explicit_base_dir_wins(self):
        with tempfile.TemporaryDirectory() as base:
            sub = Substrate(base_dir=base, mentu_bin=str(Path(base) / "nobin"))
            self.assertEqual(sub.base_dir, Path(base))


# ---------------------------------------------------------------------------
# Read-only supply path: a recording fake proves ZERO mutating calls
# ---------------------------------------------------------------------------

class TestSupplyReadOnly(unittest.TestCase):
    def _rich_fake(self):
        return RecordingSubstrate(
            patterns=[{"name": "file-history-dominance", "recurrenceCount": 7,
                       "strength": 0.9, "description": "edits cluster"}],
            signals=[{"effectiveConfidence": 0.8, "body": "build passed", "ts": "2026-06-12T00:00:00Z"}],
            claimed=[{"id": "cmt_abc", "body": "ship M2b", "owner": "agent:claude"}],
            socket=True,
        )

    def test_session_start_injects_and_makes_zero_mutating_calls(self):
        fake = self._rich_fake()
        with tempfile.TemporaryDirectory() as ws:
            d = supply_context(session_start(actor="agent:claude", cwd=ws),
                               {"substrate": fake, "cwd": ws})
        self.assertEqual(d.verb, Verb.INJECT)
        self.assertIsNotNone(d.inject_context)
        self.assertIn("CIR Patterns", d.inject_context)
        self.assertIn("Claimed commitments", d.inject_context)
        # The load-bearing assertion: not one mutating method was touched.
        touched = set(fake.calls)
        self.assertEqual(touched & RecordingSubstrate.MUTATING, set(),
                         f"supply path made mutating calls: {touched & RecordingSubstrate.MUTATING}")
        # ...and reads DID happen (proving the assertion is meaningful).
        self.assertIn("cir_patterns", touched)
        self.assertIn("cir_query", touched)
        self.assertIn("list_commitments", touched)

    def test_session_start_empty_substrate_passes(self):
        # available() True but every read empty => nothing to inject => PASS.
        fake = RecordingSubstrate(patterns=None, signals=None, claimed=[], socket=True)
        with tempfile.TemporaryDirectory() as ws:
            d = supply_context(session_start(cwd=ws), {"substrate": fake, "cwd": ws})
        self.assertEqual(d.verb, Verb.PASS)
        self.assertEqual(set(fake.calls) & RecordingSubstrate.MUTATING, set())

    def test_claimed_filtered_by_actor(self):
        fake = RecordingSubstrate(
            patterns=[{"name": "p", "recurrenceCount": 1, "strength": 0.5}],
            claimed=[{"id": "cmt_mine", "body": "x", "owner": "agent:claude"},
                     {"id": "cmt_theirs", "body": "y", "owner": "agent:other"}],
            socket=True)
        with tempfile.TemporaryDirectory() as ws:
            d = supply_context(session_start(actor="agent:claude", cwd=ws),
                               {"substrate": fake, "cwd": ws})
        self.assertEqual(d.verb, Verb.INJECT)
        self.assertIn("cmt_mine", d.inject_context)
        self.assertNotIn("cmt_theirs", d.inject_context)


# ---------------------------------------------------------------------------
# session_start lifecycle: Genesis governance surfaces in the brief
# ---------------------------------------------------------------------------

class TestSessionLifecycleGovernance(unittest.TestCase):
    def test_governed_workspace_surfaces_role(self):
        import json
        fake = RecordingSubstrate(
            patterns=[{"name": "p", "recurrenceCount": 1, "strength": 0.5}], socket=False)
        with tempfile.TemporaryDirectory() as ws:
            mentu = Path(ws) / ".mentu"
            mentu.mkdir()
            (mentu / "active_commitment").write_text("cmt_live\n")
            (mentu / "genesis.json").write_text(json.dumps({
                "actors": [{"id": "agent:*", "role": "agent"}],
                "permissions": {"agent": ["capture", "submit"]},
            }))
            d = supply_context(session_start(actor="agent:claude", cwd=ws),
                               {"substrate": fake, "cwd": ws})
        self.assertEqual(d.verb, Verb.INJECT)
        self.assertIn("cmt_live", d.inject_context)
        self.assertIn("Your role: agent", d.inject_context)
        self.assertEqual(set(fake.calls) & RecordingSubstrate.MUTATING, set())


# ---------------------------------------------------------------------------
# post_compact re-seed (file 3)
# ---------------------------------------------------------------------------

class TestCompactReseed(unittest.TestCase):
    def _write_state(self, ws, state):
        import json
        claude = Path(ws) / ".claude"
        claude.mkdir(parents=True, exist_ok=True)
        (claude / "pre-compact-state.json").write_text(json.dumps(state))

    def test_compact_injects_protocol_state(self):
        with tempfile.TemporaryDirectory() as ws:
            self._write_state(ws, {
                "active_protocols": ["review-gate", "context-isolation"],
                "step_label": "phase-2",
                "reminders": ["keep LOOP_COMPLETE"],
                "auto_review": True,
            })
            d = supply_context(post_compact(cwd=ws), {})
        self.assertEqual(d.verb, Verb.INJECT)
        self.assertIn("Protocol State (restored after compaction)", d.inject_context)
        self.assertIn("review-gate", d.inject_context)
        self.assertIn("phase-2", d.inject_context)
        self.assertIn("Auto-review is ON", d.inject_context)

    def test_compact_non_compact_source_passes(self):
        with tempfile.TemporaryDirectory() as ws:
            self._write_state(ws, {"active_protocols": ["x"]})
            d = supply_context(post_compact(cwd=ws, source="startup"), {})
        self.assertEqual(d.verb, Verb.PASS)

    def test_compact_empty_protocols_passes(self):
        with tempfile.TemporaryDirectory() as ws:
            self._write_state(ws, {"active_protocols": []})
            self.assertEqual(supply_context(post_compact(cwd=ws), {}).verb, Verb.PASS)


# ---------------------------------------------------------------------------
# pre_tool(Agent) prompt enrichment (file 2)
# ---------------------------------------------------------------------------

class TestPreToolAgentEnrichment(unittest.TestCase):
    def _write_ledger(self, ws, lines):
        mentu = Path(ws) / ".mentu"
        mentu.mkdir(parents=True, exist_ok=True)
        (mentu / "ledger.jsonl").write_text("\n".join(lines) + "\n")

    def test_enriches_prompt_with_cir_and_trust(self):
        import json
        with tempfile.TemporaryDirectory() as ws:
            self._write_ledger(ws, [
                json.dumps({"op": "capture", "payload": {"body": "found a bug"}}),
                json.dumps({"op": "annotate", "payload": {"body": "fixed it"}}),
                '{"op":"approve","payload":{}}',
                '{"kind":"warning","payload":{}}',
            ])
            d = supply_context(agent_pre_tool("Investigate X", cwd=ws), {})
        self.assertEqual(d.verb, Verb.INJECT)
        self.assertIsNotNone(d.updated_input)
        enriched = d.updated_input["prompt"]
        self.assertTrue(enriched.startswith("Investigate X"))
        self.assertIn("<cir-context>", enriched)
        self.assertIn("found a bug", enriched)
        self.assertIn("Trust state: 1 approvals, 1 warnings", enriched)

    def test_empty_prompt_passes(self):
        with tempfile.TemporaryDirectory() as ws:
            self._write_ledger(ws, ['{"op":"approve","payload":{}}'])
            self.assertEqual(supply_context(agent_pre_tool("", cwd=ws), {}).verb, Verb.PASS)

    def test_non_agent_tool_passes(self):
        ev = AgentEvent(agent="claude", event="pre_tool",
                        tool=ToolRef(name="Bash", input={"command": "ls"}))
        self.assertEqual(supply_context(ev, {}).verb, Verb.PASS)

    def test_no_ledger_passes(self):
        with tempfile.TemporaryDirectory() as ws:
            self.assertEqual(supply_context(agent_pre_tool("hi", cwd=ws), {}).verb, Verb.PASS)


# ---------------------------------------------------------------------------
# Other supply events route to PASS
# ---------------------------------------------------------------------------

class TestSupplyOtherEvents(unittest.TestCase):
    def test_prompt_submit_passes(self):
        ev = AgentEvent(agent="cursor", event="prompt_submit", prompt="refactor")
        self.assertEqual(supply_context(ev, {"substrate": RecordingSubstrate()}).verb, Verb.PASS)


# ---------------------------------------------------------------------------
# observe — classification, bodies, and the structural pass-or-annotate law
# ---------------------------------------------------------------------------

class TestObserveClassification(unittest.TestCase):
    def test_edit_is_file_modified(self):
        d = observe(post_tool("Edit", {"file_path": "/x/a.py"}))
        self.assertEqual(d.verb, Verb.ANNOTATE)
        self.assertEqual(d.annotate["kind"], "file_modified")
        self.assertEqual(d.annotate["body"], "Modified: /x/a.py")

    def test_write_is_file_created(self):
        d = observe(post_tool("Write", {"file_path": "/x/b.py"}))
        self.assertEqual(d.annotate["kind"], "file_created")
        self.assertEqual(d.annotate["body"], "Created: /x/b.py")

    def test_edit_without_path_skips(self):
        self.assertEqual(observe(post_tool("Edit", {})).verb, Verb.PASS)

    def test_bash_build_pass_and_fail(self):
        ok = observe(post_tool("Bash", {"command": "swift build"}, output="ok", exit_code=0))
        self.assertEqual(ok.annotate["kind"], "build_pass")
        self.assertTrue(ok.annotate["body"].startswith("Build passed: swift build"))
        bad = observe(post_tool("Bash", {"command": "swift build"}, exit_code=1))
        self.assertEqual(bad.annotate["kind"], "build_fail")

    def test_bash_test_pass_and_fail(self):
        ok = observe(post_tool("Bash", {"command": "pytest tests/"}, exit_code=0))
        self.assertEqual(ok.annotate["kind"], "test_pass")
        bad = observe(post_tool("Bash", {"command": "pytest tests/"}, exit_code=1,
                                event="post_tool_failure"))
        self.assertEqual(bad.annotate["kind"], "test_fail")

    def test_bash_non_build_skips(self):
        self.assertEqual(observe(post_tool("Bash", {"command": "ls -la"})).verb, Verb.PASS)

    def test_read_tool_skips(self):
        self.assertEqual(observe(post_tool("Read", {"file_path": "/x"})).verb, Verb.PASS)

    def test_output_tail_included(self):
        d = observe(post_tool("Bash", {"command": "npm test"}, output="X" * 500, exit_code=0))
        # body = header + "\n" + last 200 chars of output
        self.assertIn("X" * 200, d.annotate["body"])
        self.assertNotIn("X" * 201, d.annotate["body"])

    def test_classify_evidence_none_for_no_tool(self):
        self.assertIsNone(classify_evidence(AgentEvent(agent="claude", event="post_tool")))


class TestObserveStructuralLaw(unittest.TestCase):
    """observe is structurally incapable of deny/ask: over a deterministic
    fuzz corpus the verb is ALWAYS pass or annotate."""

    @staticmethod
    def _corpus():
        cases = [
            post_tool("Edit", {"file_path": "/x/a.py"}),
            post_tool("Write", {"file_path": "/x/b.py"}),
            post_tool("Edit", {}),
            post_tool("Edit", {"path": "/x/c.py"}),
            post_tool("Bash", {"command": "swift build"}, exit_code=0),
            post_tool("Bash", {"command": "cargo test"}, exit_code=1),
            post_tool("Bash", {"command": "ls -la"}, exit_code=0),
            post_tool("Bash", {}),
            post_tool("Read", {"file_path": "/x"}),
            post_tool("Grep", {"pattern": "x"}),
            post_tool("MultiEdit", {"file_path": "/x"}),
            AgentEvent(agent="claude", event="post_tool", tool=None),
            AgentEvent(agent="gemini", event="post_tool_failure",
                       tool=ToolRef(name="Bash", input={"command": "npm test"}, exit_code=1)),
            # tool.input a non-dict (defensive guard must hold)
            AgentEvent(agent="x", event="post_tool", tool=ToolRef(name="Edit", input=[])),
            # a dict masquerading as an event (no .tool attribute)
            {"event": "post_tool", "tool": {"name": "Edit"}},
        ]
        # Breadth: vary command/exit/path by index for >200 deterministic cases.
        for i in range(70):
            cases.append(post_tool("Bash", {"command": f"swift build target{i}"}, exit_code=i % 2))
        for i in range(70):
            cases.append(post_tool("Bash", {"command": f"run jest suite {i}"}, exit_code=i % 3))
        for i in range(50):
            cases.append(post_tool("Edit", {"file_path": f"/x/file{i}.py"}))
        for i in range(40):
            cases.append(post_tool("Read", {"file_path": f"/x/r{i}"}))
        return cases

    def test_verb_is_always_pass_or_annotate(self):
        corpus = self._corpus()
        self.assertGreaterEqual(len(corpus), 200)
        allowed = {Verb.PASS, Verb.ANNOTATE}
        for case in corpus:
            for fn in (observe, lambda e: observe_engine(e, None)):
                d = fn(case)
                self.assertIsInstance(d, Decision)
                self.assertIn(d.verb, allowed,
                              f"observe produced forbidden verb {d.verb} for {case!r}")


# ---------------------------------------------------------------------------
# Core wiring: evaluate dispatches to the real supply + observe engines
# ---------------------------------------------------------------------------

class TestCoreWiring(unittest.TestCase):
    def test_evaluate_routes_to_supply_and_observe(self):
        from mentu_policy import supply as supply_mod
        from mentu_policy import observe as observe_mod
        # Re-assert the import-time wiring (a prior test file's TestRouting may
        # have swapped in no-ops); restore it in finally.
        core.register_supply_engine(supply_mod.supply_engine)
        core.register_observe_engine(observe_mod.observe_engine)
        try:
            # session_start, no substrate => PASS through evaluate (fail-open).
            self.assertEqual(core.evaluate(session_start()).verb, Verb.PASS)
            # post_tool Edit => ANNOTATE through evaluate (observe wired).
            d = core.evaluate(post_tool("Edit", {"file_path": "/x/a.py"}))
            self.assertEqual(d.verb, Verb.ANNOTATE)
            self.assertEqual(d.annotate["kind"], "file_modified")
        finally:
            core.register_supply_engine(supply_mod.supply_engine)
            core.register_observe_engine(observe_mod.observe_engine)


if __name__ == "__main__":
    unittest.main()
