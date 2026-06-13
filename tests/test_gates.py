#!/usr/bin/env python3
"""Tests for mentu_policy.gates — the pure gate engine (M2a).

Every gate is exercised through a duck-typed FAKE Probes (canned
git_diff_text / git_changed_files / read_ledger_lines / run_build / now),
so the suite is hermetic: no subprocess, no git repo, no real ledger.
The fakes mirror the real method names/signatures in probes.py exactly.

Secret-shaped strings are assembled at runtime by concatenation
(`"sk_" + "live_" + "A"*24`), never written as literals — nothing in the
committed tree is scanner-bait. (Same discipline as tests/sandbox.py.)
"""

import json
import subprocess
import unittest
from datetime import datetime, timezone

from mentu_policy.abi import AgentEvent, ToolRef, Verb
from mentu_policy.gates import (
    check_message,
    gate_context_isolation,
    gate_engine,
    gate_review,
    gate_tool_permission,
    run_dual_triad,
    trust_from_ledger,
    validate_intent,
    validate_safety,
    validate_technical,
)

FIXED_NOW = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Synthetic-secret builders — assembled at runtime, never literal.
# Each fragment is an inert prefix or a char-repeat with no real entropy.
# ---------------------------------------------------------------------------

def _stripe_key() -> str:
    return "sk_" + "live_" + "A" * 24            # sk_(?:live|test)_[A-Za-z0-9]{20,}


def _github_token() -> str:
    return "ghp_" + "B" * 36                     # ghp_[A-Za-z0-9]{36}


def _password_line() -> str:
    return 'password = "' + "z" * 12 + '"'       # password ...['"][^'"]{8,}


def _apikey_line() -> str:
    return 'api_key = "' + "k" * 20 + '"'        # api[_-]?key ...['"][^'"]{16,}


def _token_line() -> str:
    return 'token = "' + "t" * 20 + '"'          # secret|token ...['"][^'"]{16,}


def _private_key_line() -> str:
    return "-----BEGIN " + "PRIVATE " + "KEY-----"


def _diff_with(*lines: str) -> str:
    """A minimal unified-diff body carrying the given added lines."""
    body = "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -0,0 +1 @@\n"
    return body + "".join("+" + ln + "\n" for ln in lines)


# ---------------------------------------------------------------------------
# FakeProbes — the injectable seam. Duck-types mentu_policy.probes.Probes.
# ---------------------------------------------------------------------------

class FakeProbes:
    """Canned side-effects. `raise_on` names methods that should raise (to
    drive fail-open); `build_exc`, when set, makes run_build raise it (to
    drive validate_technical's timeout/error branches)."""

    def __init__(self, *, build=(0, "", ""), build_exc=None, diff_text="",
                 changed_files=None, dirty=True, ledger_lines=None,
                 now_dt=FIXED_NOW, raise_on=None):
        self._build = build
        self._build_exc = build_exc
        self._diff_text = diff_text
        self._changed_files = list(changed_files or [])
        self._dirty = dirty
        self._ledger_lines = ledger_lines          # None => ledger file absent
        self._now_dt = now_dt
        self._raise_on = set(raise_on or ())
        self.calls = []

    def _maybe_raise(self, name):
        if name in self._raise_on:
            raise RuntimeError("probe %s exploded" % name)

    def run_build(self, cmd):
        self.calls.append(("run_build", cmd))
        if self._build_exc is not None:
            raise self._build_exc
        self._maybe_raise("run_build")
        return self._build

    def git_diff_text(self):
        self._maybe_raise("git_diff_text")
        return self._diff_text

    def git_changed_files(self):
        self._maybe_raise("git_changed_files")
        return list(self._changed_files)

    def git_status_dirty(self):
        self._maybe_raise("git_status_dirty")
        return self._dirty

    def read_ledger_lines(self, path):
        self._maybe_raise("read_ledger_lines")
        return None if self._ledger_lines is None else list(self._ledger_lines)

    def now(self):
        self._maybe_raise("now")
        return self._now_dt


class FakeGenesis:
    """Minimal governance stand-in. `exists=False` keeps the Stop gate's
    tag-based tier reclassification dormant so tests pin tier via ctx."""
    exists = False

    def __init__(self, allow_close=True):
        self._allow_close = allow_close

    def actor_allowed(self, actor, perm):
        return self._allow_close

    def classify_tier(self, tags):
        return "tier_2"

    def get_step_tier(self, _step):
        return 1


def stop_event(message="", actor="agent:claude"):
    return AgentEvent(agent="claude", event="stop", actor=actor, message=message)


def auto_review_ctx(**overrides):
    """A ctx that activates the Stop pipeline (auto_review:true)."""
    ctx = {"protocol_state": {"auto_review": True}}
    ctx.update(overrides)
    return ctx


# ---------------------------------------------------------------------------
# Dual Triad — safety validator (secret-leak + scope)
# ---------------------------------------------------------------------------

class TestValidateSafetySecrets(unittest.TestCase):
    def test_stripe_key_in_diff_fails_with_legacy_string(self):
        # Keyword-free assignment target so ONLY the Stripe pattern fires — a
        # target named for one of the keyword rules would also (correctly) fire
        # that rule, making this a two-issue case instead of the one we assert.
        probes = FakeProbes(diff_text=_diff_with('val = "' + _stripe_key() + '"'))
        result = validate_safety(probes)
        self.assertFalse(result.passed)
        self.assertEqual(result.validator, "safety")
        self.assertIn("Stripe key detected", result.details)
        self.assertEqual(result.evidence, "1 issues")

    def test_each_secret_pattern_emits_its_exact_legacy_description(self):
        probes = FakeProbes(diff_text=_diff_with(
            _password_line(), _apikey_line(), _token_line(),
            _private_key_line(),
            'stripe = "' + _stripe_key() + '"',
            'gh = "' + _github_token() + '"',
        ))
        result = validate_safety(probes)
        self.assertFalse(result.passed)
        for expected in (
            "Possible hardcoded password",
            "Possible API key",
            "Possible secret/token",
            "Private key detected",
            "Stripe key detected",
            "GitHub token detected",
        ):
            self.assertIn(expected, result.details)

    def test_clean_diff_passes(self):
        probes = FakeProbes(diff_text=_diff_with("just a normal code change"))
        result = validate_safety(probes)
        self.assertTrue(result.passed)
        self.assertEqual(result.evidence, "safe")
        self.assertIn("No secrets detected in diff", result.details)

    def test_probe_error_is_swallowed_not_raised(self):
        # validate_safety swallows its own probe fault (legacy line 152-153).
        probes = FakeProbes(raise_on={"git_diff_text"})
        result = validate_safety(probes)
        self.assertTrue(result.passed)  # no issues recorded => passes
        self.assertTrue(any("Could not check for secrets" in d for d in result.details))


class TestValidateSafetyScope(unittest.TestCase):
    def test_out_of_scope_file_flagged(self):
        probes = FakeProbes(diff_text="", changed_files=["docs/x.md"])
        result = validate_safety(probes, scope=["src/"])
        self.assertFalse(result.passed)
        self.assertIn("Out of scope: docs/x.md", result.details)

    def test_in_scope_file_allowed(self):
        probes = FakeProbes(diff_text="", changed_files=["src/app.py"])
        result = validate_safety(probes, scope=["src/"])
        self.assertTrue(result.passed)

    def test_wildcard_scope_is_inert(self):
        probes = FakeProbes(diff_text="", changed_files=["anywhere/y.md"])
        result = validate_safety(probes, scope=["*"])
        self.assertTrue(result.passed)


class TestValidateTechnical(unittest.TestCase):
    def test_build_pass(self):
        probes = FakeProbes(build=(0, "ok", ""), dirty=True)
        result = validate_technical(probes, "echo build ok")
        self.assertTrue(result.passed)
        self.assertEqual(result.evidence, "build")
        self.assertIn("Build passed", result.details)
        self.assertIn("Git working tree has changes", result.details)

    def test_build_fail(self):
        probes = FakeProbes(build=(1, "", "compile error boom"), dirty=True)
        result = validate_technical(probes, "echo build ok")
        self.assertFalse(result.passed)
        self.assertEqual(result.evidence, "build_failed")
        self.assertTrue(any("Build failed" in d for d in result.details))

    def test_build_timeout_branch(self):
        probes = FakeProbes(build_exc=subprocess.TimeoutExpired("echo build ok", 120))
        result = validate_technical(probes, "echo build ok")
        self.assertFalse(result.passed)
        self.assertIn("Build timed out (120s)", result.details)

    def test_no_git_changes_warns_but_passes(self):
        probes = FakeProbes(build=(0, "", ""), dirty=False)
        result = validate_technical(probes, "echo build ok")
        self.assertTrue(result.passed)  # build drives pass; no-change is a warning
        self.assertIn("WARNING: No git changes detected", result.details)


class TestValidateIntent(unittest.TestCase):
    def test_loop_complete_passes(self):
        result = validate_intent("work done LOOP_COMPLETE")
        self.assertTrue(result.passed)
        self.assertEqual(result.evidence, "loop_complete")

    def test_missing_loop_complete_fails(self):
        result = validate_intent("still working")
        self.assertFalse(result.passed)
        self.assertEqual(result.evidence, "incomplete")


class TestRunDualTriad(unittest.TestCase):
    def test_tier1_runs_technical_only(self):
        probes = FakeProbes(build=(0, "", ""))
        triad = run_dual_triad(tier=1, probes=probes)
        self.assertEqual([r.validator for r in triad.results], ["technical"])

    def test_tier2_adds_safety(self):
        probes = FakeProbes(build=(0, "", ""), diff_text="")
        triad = run_dual_triad(tier=2, probes=probes)
        self.assertEqual([r.validator for r in triad.results], ["technical", "safety"])

    def test_tier3_adds_intent(self):
        probes = FakeProbes(build=(0, "", ""), diff_text="")
        triad = run_dual_triad(tier=3, last_message="LOOP_COMPLETE", probes=probes)
        self.assertEqual([r.validator for r in triad.results],
                         ["technical", "safety", "intent"])
        self.assertTrue(triad.all_passed)


# ---------------------------------------------------------------------------
# Stop gate — gate_review pipeline
# ---------------------------------------------------------------------------

class TestGateReviewDeny(unittest.TestCase):
    def test_secret_in_diff_denies(self):
        probes = FakeProbes(
            build=(0, "", ""), dirty=True,
            diff_text=_diff_with('k = "' + _stripe_key() + '"'),
        )
        d = gate_review(stop_event("LOOP_COMPLETE"),
                        ctx=auto_review_ctx(probes=probes, tier=2))
        self.assertEqual(d.verb, Verb.DENY)
        self.assertTrue(d.reason)
        self.assertIn("Stripe key detected", d.reason)

    def test_out_of_scope_denies(self):
        probes = FakeProbes(
            build=(0, "", ""), dirty=True, diff_text="",
            changed_files=["docs/x.md"],
        )
        d = gate_review(stop_event("LOOP_COMPLETE"),
                        ctx=auto_review_ctx(probes=probes, tier=2, scope=["src/"]))
        self.assertEqual(d.verb, Verb.DENY)
        self.assertIn("Out of scope: docs/x.md", d.reason)

    def test_failure_emits_single_reopen_ledger_annotation(self):
        # Technical build failure with an active commitment + existing ledger
        # => exactly one annotate{kind:"ledger"} whose op is "reopen".
        probes = FakeProbes(build=(1, "", "boom"), dirty=True, ledger_lines=[])
        d = gate_review(
            stop_event("LOOP_COMPLETE"),
            ctx=auto_review_ctx(probes=probes, tier=1,
                                active_commitment="cmt_test", workspace="subtrace"),
        )
        self.assertEqual(d.verb, Verb.DENY)
        self.assertTrue(d.reason)
        self.assertIsInstance(d.annotate, dict)
        self.assertEqual(d.annotate["kind"], "ledger")
        op = json.loads(d.annotate["body"])
        self.assertEqual(op["op"], "reopen")
        self.assertEqual(op["payload"]["commitment"], "cmt_test")
        self.assertTrue(op["payload"]["reason"])
        meta = op["payload"]["meta"]
        self.assertEqual(meta["basis"], ["technical"])
        self.assertEqual(meta["failure_count"], 1)
        self.assertEqual(meta["tier"], 1)

    def test_deny_without_ledger_has_no_annotation(self):
        # No active commitment => deny carries a reason but no ledger op.
        probes = FakeProbes(build=(1, "", "boom"), dirty=True)
        d = gate_review(stop_event("LOOP_COMPLETE"),
                        ctx=auto_review_ctx(probes=probes, tier=1))
        self.assertEqual(d.verb, Verb.DENY)
        self.assertTrue(d.reason)
        self.assertIsNone(d.annotate)


class TestGateReviewPass(unittest.TestCase):
    def test_auto_review_off_passes(self):
        probes = FakeProbes()
        d = gate_review(stop_event("LOOP_COMPLETE"), ctx={"probes": probes})
        self.assertEqual(d.verb, Verb.PASS)

    def test_no_protocol_state_passes(self):
        probes = FakeProbes()
        d = gate_review(stop_event("LOOP_COMPLETE"), ctx={"probes": probes})
        self.assertEqual(d.verb, Verb.PASS)

    def test_all_checks_pass_auto_close(self):
        probes = FakeProbes(build=(0, "", ""), dirty=True, ledger_lines=[])
        d = gate_review(
            stop_event("all good LOOP_COMPLETE"),
            ctx=auto_review_ctx(probes=probes, tier=1,
                                active_commitment="cmt_ok", workspace="subtrace"),
        )
        self.assertEqual(d.verb, Verb.ALLOW)
        self.assertEqual(d.annotate["kind"], "ledger")
        self.assertEqual(json.loads(d.annotate["body"])["op"], "close")

    def test_actor_without_close_permission_downgrades_to_submit(self):
        probes = FakeProbes(build=(0, "", ""), dirty=True, ledger_lines=[])
        d = gate_review(
            stop_event("all good LOOP_COMPLETE"),
            ctx=auto_review_ctx(probes=probes, tier=1, active_commitment="cmt_sub",
                                workspace="subtrace",
                                genesis=FakeGenesis(allow_close=False)),
        )
        self.assertEqual(d.verb, Verb.ALLOW)
        self.assertEqual(json.loads(d.annotate["body"])["op"], "submit")

    def test_tier3_defers_to_human_without_ledger_write(self):
        probes = FakeProbes(build=(0, "", ""), dirty=True, diff_text="", ledger_lines=[])
        d = gate_review(
            stop_event("LOOP_COMPLETE"),
            ctx=auto_review_ctx(probes=probes, tier=3, active_commitment="cmt_t3",
                                workspace="subtrace"),
        )
        self.assertEqual(d.verb, Verb.ALLOW)
        self.assertIsNone(d.annotate)
        self.assertIn("human approval", d.reason)


# ---------------------------------------------------------------------------
# SubagentStop gate — gate_context_isolation
# ---------------------------------------------------------------------------

class TestContextIsolation(unittest.TestCase):
    def _msg(self, n):
        return "\n".join("line %d" % i for i in range(n))

    def test_201_lines_denies_with_redirect_annotation(self):
        ev = AgentEvent(agent="claude", event="subagent_stop", message=self._msg(201))
        d = gate_context_isolation(ev)
        self.assertEqual(d.verb, Verb.DENY)
        self.assertTrue(d.reason)
        self.assertIsInstance(d.annotate, dict)
        self.assertEqual(d.annotate["kind"], "isolation_redirect")
        self.assertIn("201 lines", d.annotate["body"])

    def test_200_lines_is_the_boundary_not_denied(self):
        ev = AgentEvent(agent="claude", event="subagent_stop", message=self._msg(200))
        d = gate_context_isolation(ev)
        self.assertNotEqual(d.verb, Verb.DENY)
        self.assertEqual(d.verb, Verb.ALLOW)

    def test_empty_message_passes(self):
        ev = AgentEvent(agent="claude", event="subagent_stop", message="")
        self.assertEqual(gate_context_isolation(ev).verb, Verb.PASS)

    def test_check_message_line_limit_string(self):
        self.assertIsNone(check_message(self._msg(200)))
        self.assertIn("limit: 200", check_message(self._msg(201)))


# ---------------------------------------------------------------------------
# PreToolUse gate — gate_tool_permission + trust math
# ---------------------------------------------------------------------------

def bash_event(command):
    return AgentEvent(agent="claude", event="pre_tool",
                      tool=ToolRef(name="Bash", input={"command": command}))


def write_event(path):
    return AgentEvent(agent="claude", event="pre_tool",
                      tool=ToolRef(name="Write", input={"file_path": path}))


class TestToolPermission(unittest.TestCase):
    def test_high_trust_allows(self):
        d = gate_tool_permission(bash_event("ls -la"), trust=0.85)
        self.assertEqual(d.verb, Verb.ALLOW)
        self.assertTrue(d.reason)
        self.assertIn("above threshold", d.reason)

    def test_mid_trust_passes(self):
        d = gate_tool_permission(bash_event("ls -la"), trust=0.50)
        self.assertEqual(d.verb, Verb.PASS)

    def test_low_trust_denies(self):
        d = gate_tool_permission(bash_event("ls -la"), trust=0.33)
        self.assertEqual(d.verb, Verb.DENY)
        self.assertTrue(d.reason)
        self.assertIn("below threshold", d.reason)

    def test_destructive_command_raises_the_allow_bar(self):
        # rm -rf at 0.85 would allow a normal command, but the destructive
        # bar is 0.90 — so it falls through to pass instead of allow.
        d = gate_tool_permission(bash_event("rm -rf /tmp/x"), trust=0.85)
        self.assertEqual(d.verb, Verb.PASS)

    def test_destructive_command_allows_above_raised_bar(self):
        d = gate_tool_permission(bash_event("rm -rf /tmp/x"), trust=0.95)
        self.assertEqual(d.verb, Verb.ALLOW)

    def test_destructive_write_raises_the_allow_bar(self):
        d = gate_tool_permission(write_event(".env"), trust=0.85)
        self.assertEqual(d.verb, Verb.PASS)


class TestTrustFromLedger(unittest.TestCase):
    def _line(self, kind):
        return {
            "approve": '{"op":"approve","payload":{}}',
            "warn": '{"kind":"warning","payload":{}}',
            "block": '{"op":"reopen","payload":{"reason":"BLOCKED here"}}',
        }[kind]

    def test_none_ledger_is_neutral(self):
        self.assertEqual(trust_from_ledger(None), 0.50)

    def test_empty_ledger_is_neutral(self):
        self.assertEqual(trust_from_ledger([]), 0.50)

    def test_all_approvals_is_full_trust(self):
        self.assertEqual(trust_from_ledger([self._line("approve")]), 1.00)

    def test_mixed_truncating_division(self):
        lines = ([self._line("approve")] * 3
                 + [self._line("warn")] + [self._line("block")])
        # 3 approve / (3+1+1) total => 300 // 5 / 100 == 0.60
        self.assertEqual(trust_from_ledger(lines), 0.60)

    def test_no_approvals_is_zero(self):
        lines = [self._line("warn"), self._line("block")]
        self.assertEqual(trust_from_ledger(lines), 0.0)


# ---------------------------------------------------------------------------
# gate_engine — the dispatcher core.evaluate routes gate events to
# ---------------------------------------------------------------------------

class TestGateEngine(unittest.TestCase):
    def test_no_ctx_is_deterministic_pass(self):
        for kind in ("stop", "subagent_stop", "pre_tool", "permission_request"):
            ev = AgentEvent(agent="claude", event=kind,
                            tool=ToolRef(name="Bash", input={"command": "ls"}))
            self.assertEqual(gate_engine(ev, None).verb, Verb.PASS,
                             "%s with no ctx must PASS" % kind)

    def test_pre_tool_explicit_trust_allows(self):
        ev = bash_event("ls -la")
        self.assertEqual(gate_engine(ev, {"trust": 0.85}).verb, Verb.ALLOW)

    def test_pre_tool_explicit_trust_denies(self):
        ev = bash_event("ls -la")
        self.assertEqual(gate_engine(ev, {"trust": 0.33}).verb, Verb.DENY)

    def test_pre_tool_derives_trust_from_ledger(self):
        # ledger with 0 approvals / 2 warnings => trust 0.0 => deny
        probes = FakeProbes(ledger_lines=[
            '{"kind":"warning","payload":{}}',
            '{"kind":"warning","payload":{}}',
        ])
        d = gate_engine(bash_event("ls -la"), {"probes": probes})
        self.assertEqual(d.verb, Verb.DENY)

    def test_subagent_stop_inactive_passes(self):
        ev = AgentEvent(agent="claude", event="subagent_stop",
                        message="\n".join("x" for _ in range(300)))
        self.assertEqual(gate_engine(ev, {"isolation_active": False}).verb, Verb.PASS)

    def test_subagent_stop_active_denies_oversized(self):
        ev = AgentEvent(agent="claude", event="subagent_stop",
                        message="\n".join("x" for _ in range(300)))
        d = gate_engine(ev, {"isolation_active": True})
        self.assertEqual(d.verb, Verb.DENY)
        self.assertEqual(d.annotate["kind"], "isolation_redirect")

    def test_stop_routes_to_review(self):
        probes = FakeProbes(build=(1, "", "boom"), dirty=True,
                            diff_text=_diff_with('k = "' + _stripe_key() + '"'))
        d = gate_engine(stop_event("LOOP_COMPLETE"),
                        auto_review_ctx(probes=probes, tier=2))
        self.assertEqual(d.verb, Verb.DENY)

    def test_unknown_event_passes(self):
        ev = AgentEvent(agent="claude", event="session_start")
        self.assertEqual(gate_engine(ev, {}).verb, Verb.PASS)


# ---------------------------------------------------------------------------
# Fail-open — an internal exception in any gate degrades to PASS, never raises
# ---------------------------------------------------------------------------

class ExplodingMessage:
    """An event-like object whose .message access raises."""
    event = "subagent_stop"

    @property
    def message(self):
        raise RuntimeError("message access explodes")


class ExplodingEvent:
    """An event-like object whose .event access raises (for the dispatcher)."""
    @property
    def event(self):
        raise RuntimeError("event access explodes")


class TestFailOpen(unittest.TestCase):
    def test_gate_review_failopen_on_probe_raise(self):
        probes = FakeProbes(raise_on={"read_ledger_lines"})
        d = gate_review(stop_event("LOOP_COMPLETE"),
                        ctx=auto_review_ctx(probes=probes, tier=2))
        self.assertEqual(d.verb, Verb.PASS)

    def test_gate_context_isolation_failopen(self):
        d = gate_context_isolation(ExplodingMessage())
        self.assertEqual(d.verb, Verb.PASS)

    def test_gate_tool_permission_failopen(self):
        # An uncomparable trust forces a TypeError inside the gate body.
        d = gate_tool_permission(bash_event("ls -la"), trust=object())
        self.assertEqual(d.verb, Verb.PASS)

    def test_gate_engine_failopen_on_broken_event(self):
        self.assertEqual(gate_engine(ExplodingEvent(), {}).verb, Verb.PASS)

    def test_gate_engine_failopen_on_probe_raise(self):
        probes = FakeProbes(raise_on={"read_ledger_lines"})
        d = gate_engine(bash_event("ls -la"), {"probes": probes})
        self.assertEqual(d.verb, Verb.PASS)


# ---------------------------------------------------------------------------
# Contract — every deny carries a non-empty reason
# ---------------------------------------------------------------------------

class TestDenyContract(unittest.TestCase):
    def test_every_deny_has_a_nonempty_reason(self):
        secret = FakeProbes(build=(0, "", ""), dirty=True,
                            diff_text=_diff_with('k = "' + _stripe_key() + '"'))
        scope = FakeProbes(build=(0, "", ""), dirty=True, diff_text="",
                           changed_files=["docs/x.md"])
        reopen = FakeProbes(build=(1, "", "boom"), dirty=True, ledger_lines=[])
        long_msg = AgentEvent(agent="claude", event="subagent_stop",
                              message="\n".join("x" for _ in range(250)))

        denies = [
            gate_review(stop_event("LOOP_COMPLETE"),
                        ctx=auto_review_ctx(probes=secret, tier=2)),
            gate_review(stop_event("LOOP_COMPLETE"),
                        ctx=auto_review_ctx(probes=scope, tier=2, scope=["src/"])),
            gate_review(stop_event("LOOP_COMPLETE"),
                        ctx=auto_review_ctx(probes=reopen, tier=1,
                                            active_commitment="cmt_x")),
            gate_context_isolation(long_msg),
            gate_tool_permission(bash_event("ls -la"), trust=0.10),
        ]
        for d in denies:
            self.assertEqual(d.verb, Verb.DENY)
            self.assertTrue(d.reason, "deny must carry a non-empty reason")


if __name__ == "__main__":
    unittest.main()
