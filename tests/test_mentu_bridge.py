#!/usr/bin/env python3
"""Tests for mentu_policy.adapters.mentu — the M5 HarnessV1 bridge.

Fixture-driven: HarnessV1 stream-part dicts in ``tests/fixtures/harness_v1/`` are
mapped to ``AgentEvent``s and evaluated through the SAME policy core the foreign
adapters use. Proves the §M5 acceptance criteria:

  * each HarnessV1 part maps to the event the mapping table specifies;
  * ONE core, TWO agents — the same offending event yields the same
    ``Decision.verb == DENY`` whether ``agent=claude`` or ``agent=mentu`` (and a
    clean event yields the same ALLOW);
  * a ``deny`` refuses at the EXISTING approval boundary
    (``submitToolApproval`` ``approved=False``) — never a mid-flight abort;
  * mentu is the only all-``True`` capability row besides claude;
  * importing the bridge performs no live I/O.

Synthetic secrets are assembled at runtime (never literal) — same discipline as
test_gates.py / sandbox.py.
"""

import ast
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from mentu_policy import core, gates
from mentu_policy.abi import AgentEvent, Decision, ToolRef, Verb
from mentu_policy.adapters import mentu
from mentu_policy.capabilities import CAPABILITIES

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "harness_v1"
TIERS = ("observe", "supply_context", "gate", "compaction")
FIXED_NOW = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def setUpModule():
    """Re-assert the real M2a gate engine into core.evaluate's dispatch. A prior
    test module (test_evaluate_failopen) resets it to the M1 no-op in its
    ``finally`` blocks, so any module that drives a real gate verdict must
    re-register first (mirrors test_parity_claude.py)."""
    core.register_gate_engine(gates.gate_engine)


# ---------------------------------------------------------------------------
# Synthetic-secret + fake-probe seam (runtime-assembled; never literal).
# ---------------------------------------------------------------------------

def _stripe_key() -> str:
    return "sk_" + "live_" + "A" * 24            # sk_(?:live|test)_[A-Za-z0-9]{20,}


def _diff_with(*lines: str) -> str:
    body = "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -0,0 +1 @@\n"
    return body + "".join("+" + ln + "\n" for ln in lines)


class FakeProbes:
    """Canned side-effects; duck-types mentu_policy.probes.Probes. No subprocess,
    no git, no real ledger."""

    def __init__(self, *, build=(0, "", ""), diff_text="", dirty=True,
                 ledger_lines=None):
        self._build = build
        self._diff_text = diff_text
        self._dirty = dirty
        self._ledger = ledger_lines        # None => ledger file absent

    def run_build(self, cmd):
        return self._build

    def git_diff_text(self):
        return self._diff_text

    def git_changed_files(self):
        return []

    def git_status_dirty(self):
        return self._dirty

    def read_ledger_lines(self, path):
        return None if self._ledger is None else list(self._ledger)

    def now(self):
        return FIXED_NOW


def _review_ctx(*, secret: bool):
    """A ctx that activates the Stop pipeline (auto_review) at tier 2 (so the
    safety/secret validator runs). ``secret`` toggles a synthetic Stripe key in
    the diff."""
    diff = _diff_with('k = "' + _stripe_key() + '"') if secret \
        else _diff_with("an ordinary, secret-free change")
    return {
        "protocol_state": {"auto_review": True},
        "probes": FakeProbes(build=(0, "", ""), dirty=True, diff_text=diff),
        "tier": 2,
    }


# ---------------------------------------------------------------------------
# (1) Fixture mapping — each committed HarnessV1 part -> expected AgentEvent.
# ---------------------------------------------------------------------------

class TestFixtureMapping(unittest.TestCase):
    EXPECTED = {
        "tool-call": "pre_tool",
        "tool-approval-request": "permission_request",
        "tool-result": "post_tool",
        "compaction": "post_compact",
        "finish": "stop",
    }

    def _load(self, name):
        return json.loads((FIXTURES / (name + ".json")).read_text())

    def test_all_five_fixtures_present(self):
        present = {p.stem for p in FIXTURES.glob("*.json")}
        self.assertEqual(present, set(self.EXPECTED))

    def test_each_fixture_maps_to_expected_event(self):
        for name, expected in self.EXPECTED.items():
            ev = mentu._to_agent_event(self._load(name), {})
            self.assertIsInstance(ev, AgentEvent, name)
            self.assertEqual(ev.event, expected, name)
            self.assertEqual(ev.agent, "mentu", name)

    def test_tool_call_carries_toolref_name_and_input(self):
        ev = mentu._to_agent_event(self._load("tool-call"), {})
        self.assertIsInstance(ev.tool, ToolRef)
        self.assertEqual(ev.tool.name, "Bash")
        self.assertEqual(ev.tool.input, {"command": "echo mentu-harness-demo"})

    def test_tool_approval_request_carries_toolref(self):
        ev = mentu._to_agent_event(self._load("tool-approval-request"), {})
        self.assertEqual(ev.tool.name, "Write")
        self.assertEqual(ev.tool.input.get("file_path"), "docs/demo.md")

    def test_tool_result_lifts_output(self):
        ev = mentu._to_agent_event(self._load("tool-result"), {})
        self.assertEqual(ev.tool.name, "Bash")
        self.assertEqual(ev.tool.output, "mentu-harness-demo\n")


# ---------------------------------------------------------------------------
# (2) Full mapping table — the rows with no committed fixture, plus the
#     "unknown -> None" floor.
# ---------------------------------------------------------------------------

class TestMappingTableComplete(unittest.TestCase):
    def test_dostart_is_session_start(self):
        self.assertEqual(mentu._to_agent_event({"type": "doStart"}, {}).event,
                         "session_start")

    def test_user_message_is_prompt_submit(self):
        ev = mentu._to_agent_event(
            {"type": "message", "role": "user", "text": "do the thing"}, {})
        self.assertEqual(ev.event, "prompt_submit")
        self.assertEqual(ev.prompt, "do the thing")

    def test_dostop_is_session_end(self):
        self.assertEqual(mentu._to_agent_event({"type": "doStop"}, {}).event,
                         "session_end")

    def test_compaction_pre_phase_is_pre_compact(self):
        self.assertEqual(
            mentu._to_agent_event({"type": "compaction", "phase": "pre"}, {}).event,
            "pre_compact")

    def test_tool_result_error_is_post_tool_failure(self):
        ev = mentu._to_agent_event(
            {"type": "tool-result", "toolName": "Bash", "isError": True,
             "output": "boom"}, {})
        self.assertEqual(ev.event, "post_tool_failure")
        self.assertEqual(ev.tool.output, "boom")

    def test_tool_error_part_is_post_tool_failure(self):
        ev = mentu._to_agent_event(
            {"type": "tool-error", "toolName": "Bash", "error": "nonzero exit"}, {})
        self.assertEqual(ev.event, "post_tool_failure")

    def test_provider_executed_tool_call_is_skipped(self):
        # providerExecuted=true is not a host pre-dispatch -> no pre_tool gate.
        ev = mentu._to_agent_event(
            {"type": "tool-call", "toolName": "web_search",
             "input": {}, "providerExecuted": True}, {})
        self.assertIsNone(ev)

    def test_unknown_part_is_none(self):
        self.assertIsNone(mentu._to_agent_event({"type": "telemetry-ping"}, {}))

    def test_non_dict_is_none(self):
        self.assertIsNone(mentu._to_agent_event(None, {}))
        self.assertIsNone(mentu._to_agent_event("not-a-part", {}))

    def test_ctx_actor_is_lifted_else_defaults_to_mentu(self):
        ev = mentu._to_agent_event({"type": "doStart"}, {})
        self.assertEqual(ev.actor, "agent:mentu")
        ev2 = mentu._to_agent_event({"type": "doStart"}, {"actor": "agent:codex"})
        self.assertEqual(ev2.actor, "agent:codex")


# ---------------------------------------------------------------------------
# (3) Decision -> HarnessV1 control encoding.
# ---------------------------------------------------------------------------

class TestToHarnessControl(unittest.TestCase):
    def test_deny_refuses_at_the_existing_approval_boundary(self):
        ctl = mentu._to_harness_control(Decision.deny("staged a credential"))
        self.assertEqual(ctl, {"control": "submitToolApproval",
                               "approved": False, "reason": "staged a credential"})

    def test_allow_approves_without_a_reason_field(self):
        ctl = mentu._to_harness_control(Decision.allow("all checks passed"))
        self.assertEqual(ctl, {"control": "submitToolApproval", "approved": True})

    def test_ask_defers_to_operator(self):
        ctl = mentu._to_harness_control(Decision.ask("needs a human"))
        self.assertEqual(ctl, {"control": "deferToOperator",
                               "reason": "needs a human"})

    def test_inject_supplies_context(self):
        ctl = mentu._to_harness_control(Decision.supply(md="## brief", prompt="p"))
        self.assertEqual(ctl, {"control": "supplyContext", "context": "## brief",
                               "updatedInput": {"prompt": "p"}})

    def test_annotate_is_a_noop_control(self):
        self.assertIsNone(mentu._to_harness_control(Decision.note("audit", "x")))

    def test_pass_is_a_noop_control(self):
        self.assertIsNone(mentu._to_harness_control(Decision.pass_()))


# ---------------------------------------------------------------------------
# (4) One core, two agents — the §M5 same-verdict parity property.
# ---------------------------------------------------------------------------

class TestSameVerdictParity(unittest.TestCase):
    """The core routes by event kind and never inspects ``event.agent``; a
    native (mentu) run and a foreign (claude) run therefore get an IDENTICAL
    decision for the same offending / clean action."""

    def _stop_event(self, agent):
        # Identical in every field EXCEPT `agent`, so `agent` is the only
        # variable — isolating the "core is agent-agnostic" claim.
        return AgentEvent(agent=agent, event="stop", actor="agent:claude",
                          message="LOOP_COMPLETE")

    def test_offending_event_denies_identically_for_claude_and_mentu(self):
        ctx = _review_ctx(secret=True)
        d_claude = core.evaluate(self._stop_event("claude"), ctx)
        d_mentu = core.evaluate(self._stop_event("mentu"), ctx)
        self.assertEqual(d_claude.verb, Verb.DENY)
        self.assertEqual(d_mentu.verb, Verb.DENY)
        self.assertEqual(d_claude.verb, d_mentu.verb)
        self.assertEqual(d_claude.reason, d_mentu.reason)   # byte-identical
        self.assertIn("Stripe key detected", d_mentu.reason)

    def test_clean_event_allows_identically_for_claude_and_mentu(self):
        ctx = _review_ctx(secret=False)
        d_claude = core.evaluate(self._stop_event("claude"), ctx)
        d_mentu = core.evaluate(self._stop_event("mentu"), ctx)
        self.assertEqual(d_claude.verb, Verb.ALLOW)
        self.assertEqual(d_mentu.verb, Verb.ALLOW)
        self.assertEqual(d_claude.verb, d_mentu.verb)


# ---------------------------------------------------------------------------
# (5) End-to-end through the seam: on_harness_event(part, ctx) -> control dict.
# ---------------------------------------------------------------------------

class TestOnHarnessEventEndToEnd(unittest.TestCase):
    def test_offending_finish_denies_at_approval_boundary(self):
        # A native run's turn-close (finish) that staged a credential is refused
        # at the EXISTING approval boundary — not a mid-flight abort.
        part = {"type": "finish", "finishReason": "stop", "text": "LOOP_COMPLETE"}
        ctl = mentu.on_harness_event(part, _review_ctx(secret=True))
        self.assertEqual(ctl["control"], "submitToolApproval")
        self.assertIs(ctl["approved"], False)
        self.assertIn("Stripe key detected", ctl["reason"])

    def test_clean_finish_allows(self):
        part = {"type": "finish", "finishReason": "stop", "text": "LOOP_COMPLETE"}
        ctl = mentu.on_harness_event(part, _review_ctx(secret=False))
        self.assertEqual(ctl, {"control": "submitToolApproval", "approved": True})

    def test_pre_tool_with_no_trust_context_is_a_noop(self):
        # No ledger/trust in ctx -> neutral trust band -> PASS -> no control.
        part = {"type": "tool-call", "toolName": "Bash",
                "input": {"command": "ls"}, "providerExecuted": False}
        self.assertIsNone(mentu.on_harness_event(part, {}))

    def test_unknown_part_is_a_noop(self):
        self.assertIsNone(mentu.on_harness_event({"type": "telemetry-ping"}, {}))


# ---------------------------------------------------------------------------
# (6) Capability registry — mentu + claude are the only all-True rows.
# ---------------------------------------------------------------------------

class TestCapabilityRegistry(unittest.TestCase):
    def test_all_true_rows_are_exactly_claude_and_mentu(self):
        # The spec's Appendix C resolved by the M1 suite (test_capabilities.py):
        # mentu is full-capability; no OTHER agent (codex/cursor/gemini) is.
        all_true = {
            agent
            for agent, caps in CAPABILITIES.items()
            if all(caps.get(t) is True for t in TIERS)
        }
        self.assertEqual(all_true, {"claude", "mentu"})

    def test_mentu_row_is_all_true(self):
        self.assertTrue(all(CAPABILITIES["mentu"].get(t) is True for t in TIERS))


# ---------------------------------------------------------------------------
# (7) Import purity — the bridge does no live I/O at import time.
# ---------------------------------------------------------------------------

class TestImportPurity(unittest.TestCase):
    def test_no_socket_or_subprocess_in_module_namespace(self):
        # The bridge never pulls a socket/subprocess/substrate name into its own
        # namespace (the M3 capture path only needs os + io).
        for name in ("socket", "subprocess", "Substrate"):
            self.assertFalse(hasattr(mentu, name),
                             "bridge must not import %s at module level" % name)

    def test_no_top_level_calls_at_import(self):
        # Any I/O at import would be a module-level call expression. Assert there
        # are NONE outside function/class bodies (imports + constant assignments
        # only). Comments/docstrings carry no Call nodes, so a docstring that
        # *names* socket/subprocess does not trip this.
        tree = ast.parse(Path(mentu.__file__).read_text())
        offending = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
                continue
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    fn = sub.func
                    offending.append(getattr(fn, "id", getattr(fn, "attr", "?")))
        self.assertEqual(offending, [],
                         "module-level call(s) at import time: %r" % offending)


if __name__ == "__main__":
    unittest.main()
