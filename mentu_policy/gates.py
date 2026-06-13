#!/usr/bin/env python3
"""mentu_policy.gates — the pure gate engine (M2a).

Rule bodies extracted VERBATIM from the legacy Claude-Code hooks
(hooks/review_gate.py, hooks/dual_triad_validator.py,
hooks/context_isolation_gate.py, hooks/pre-tool-use-permission.sh);
only the I/O envelope moved out:

  - stdin/env reads      -> AgentEvent fields + the `ctx` dict
  - subprocess calls     -> the injectable probes seam (probes.py)
  - sys.exit / print     -> a returned Decision verb
  - ledger WRITES        -> a RETURNED annotation {kind:"ledger", body:<op>}
                            (policy-core never writes the ledger; the
                            adapter persists the annotation)

Every gate wraps its body fail-open: an internal exception returns
Decision.pass_() — an infrastructure fault never refuses the user's own
work. No native wire encodings (no permission-decision JSON, no exit
codes) live here; that is adapter territory (M3).

The behavioral baseline for all of this is pinned by the golden-vector
harness (scripts/capture-golden-vectors.sh) against verbatim snapshots
of the legacy scripts in tests/fixtures/legacy/.
"""
from __future__ import annotations

import functools
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import List, Optional

from .abi import AgentEvent, Decision, Verb
from . import core


def _fail_open(fn):
    """Internal exception -> Decision.pass_() (mirrors the legacy scripts'
    `except Exception: sys.exit(0)` outermost guard)."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except BaseException:
            return Decision.pass_()
    return wrapper


# ---------------------------------------------------------------------------
# Dual Triad validators — ported from hooks/dual_triad_validator.py
# ---------------------------------------------------------------------------

@dataclass
class ValidationResult:
    validator: str  # "technical" | "safety" | "intent"
    passed: bool
    evidence: str
    details: List[str] = field(default_factory=list)


@dataclass
class TriadResult:
    tier: int
    results: List[ValidationResult]

    @property
    def all_passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def failures(self) -> List[ValidationResult]:
        return [r for r in self.results if not r.passed]

    def format_feedback(self) -> str:
        """Format validation results as feedback for the agent."""
        lines = []
        for r in self.results:
            status = "PASS" if r.passed else "FAIL"
            lines.append(f"  {r.validator}: {status} — {r.evidence}")
            for d in r.details:
                lines.append(f"    {d}")
        return "\n".join(lines)


def read_build_cmd(claude_md_text: Optional[str]) -> str:
    """Extract build command from CLAUDE.md text (legacy read the file;
    the pure core takes the text via ctx). None == file absent."""
    if claude_md_text is None:
        return "echo build ok"
    match = re.search(r"## Commands.*?```bash\n(.+?)\n```", claude_md_text, re.DOTALL)
    if match:
        return match.group(1).strip().split("\n")[0]
    return "echo build ok"


def validate_technical(probes, build_cmd: str = "echo build ok") -> ValidationResult:
    """Technical validator: does it build? Do tests pass?"""
    details = []

    try:
        returncode, _stdout, stderr = probes.run_build(build_cmd)
        build_ok = returncode == 0
        if not build_ok:
            details.append(f"Build failed: {stderr[:200]}")
        else:
            details.append("Build passed")
    except Exception as e:
        # The probe raises subprocess.TimeoutExpired through; preserve the
        # legacy timeout message, generic error otherwise.
        if type(e).__name__ == "TimeoutExpired":
            build_ok = False
            details.append("Build timed out (120s)")
        else:
            build_ok = False
            details.append(f"Build error: {e}")

    # Check git has changes (agent didn't no-op) — via the status probe
    try:
        has_changes = probes.git_status_dirty()
        if has_changes:
            details.append("Git working tree has changes")
        else:
            details.append("WARNING: No git changes detected")
    except Exception:
        pass

    return ValidationResult(
        validator="technical",
        passed=build_ok,
        evidence="build" if build_ok else "build_failed",
        details=details
    )


def validate_safety(probes, scope: Optional[List[str]] = None) -> ValidationResult:
    """Safety validator: stayed in scope? No secrets? No dangerous patterns?"""
    details = []
    issues = []

    # Check for secrets in staged/modified files
    try:
        diff_text = probes.git_diff_text()

        # Secret patterns
        secret_patterns = [
            (r"(?:password|passwd|pwd)\s*[:=]\s*['\"][^'\"]{8,}", "Possible hardcoded password"),
            (r"(?:api[_-]?key|apikey)\s*[:=]\s*['\"][^'\"]{16,}", "Possible API key"),
            (r"(?:secret|token)\s*[:=]\s*['\"][^'\"]{16,}", "Possible secret/token"),
            (r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----", "Private key detected"),
            (r"sk_(?:live|test)_[a-zA-Z0-9]{20,}", "Stripe key detected"),
            (r"ghp_[a-zA-Z0-9]{36}", "GitHub token detected"),
        ]

        for pattern, description in secret_patterns:
            if re.search(pattern, diff_text, re.IGNORECASE):
                issues.append(description)

        if not issues:
            details.append("No secrets detected in diff")

    except Exception as e:
        details.append(f"Could not check for secrets: {e}")

    # Check scope (if specified)
    if scope and scope != ["*"]:
        try:
            for filepath in probes.git_changed_files():
                in_scope = any(
                    filepath.startswith(s.rstrip("/")) or s == "*"
                    for s in scope
                )
                if not in_scope:
                    issues.append(f"Out of scope: {filepath}")
        except Exception:
            pass

    passed = len(issues) == 0
    if issues:
        details.extend(issues)
    else:
        details.append("All safety checks passed")

    return ValidationResult(
        validator="safety",
        passed=passed,
        evidence="safe" if passed else f"{len(issues)} issues",
        details=details
    )


def validate_intent(last_message: Optional[str] = None) -> ValidationResult:
    """Intent validator: does the work match the original vision?"""
    details = []

    # Check LOOP_COMPLETE as proxy for intent completion
    loop_complete = False
    if last_message:
        loop_complete = "LOOP_COMPLETE" in last_message

    if loop_complete:
        details.append("LOOP_COMPLETE found in agent output")
    else:
        details.append("LOOP_COMPLETE not found — step may be incomplete")

    return ValidationResult(
        validator="intent",
        passed=loop_complete,
        evidence="loop_complete" if loop_complete else "incomplete",
        details=details
    )


def run_dual_triad(
    tier: int = 1,
    scope: Optional[List[str]] = None,
    last_message: Optional[str] = None,
    *,
    probes,
    build_cmd: str = "echo build ok",
) -> TriadResult:
    """Run the Dual Triad validation at the specified tier."""
    results = []

    # Technical (always)
    results.append(validate_technical(probes, build_cmd))

    # Safety (Tier 2+)
    if tier >= 2:
        results.append(validate_safety(probes, scope=scope))

    # Intent (Tier 3)
    if tier >= 3:
        results.append(validate_intent(last_message=last_message))

    return TriadResult(tier=tier, results=results)


# ---------------------------------------------------------------------------
# Stop gate — ported from hooks/review_gate.py
# ---------------------------------------------------------------------------

def check_loop_complete(message: str) -> Optional[str]:
    """Check if LOOP_COMPLETE is present in the final message."""
    if "LOOP_COMPLETE" in message:
        return None
    return "Missing LOOP_COMPLETE marker in final message. Add LOOP_COMPLETE when work is done."


def check_git_changes(probes) -> Optional[str]:
    """Check if git working tree has changes."""
    try:
        if probes.git_status_dirty():
            return None  # Has changes — good
        return "No git changes detected. The agent may have no-op'd."
    except Exception:
        return None  # Can't check — allow


def check_context_doc(step_label: str, context_docs) -> Optional[str]:
    """Check if the agent updated its CONTEXT doc section. The legacy hook
    globbed docs/CONTEXT-*.md from disk; the pure core takes (name, text)
    pairs via ctx["context_docs"]."""
    for name, text in (context_docs or []):
        # Look for placeholder comments that should have been replaced
        if f"<!-- Updated by {step_label}" in text:
            return f"CONTEXT doc {name} has unfilled section for {step_label}. Update your Phase section before exiting."
    return None  # Either updated or no matching section


def _ledger_op(op: str, ts: str, active_cmt: str, actor: str, workspace: str,
               payload: dict) -> str:
    """Serialize a ledger op exactly the way the legacy gate did (same id
    derivation, same key order, same json.dumps defaults)."""
    op_id = f"op_{hashlib.md5(f'{ts}{active_cmt}{op}'.encode()).hexdigest()[:8]}"
    return json.dumps({
        "id": op_id, "op": op, "ts": ts,
        "actor": actor, "workspace": workspace,
        "payload": payload
    })


def _effective_tier(ctx) -> int:
    tier = ctx.get("tier")
    if isinstance(tier, int):
        return tier
    genesis = ctx.get("genesis")
    if genesis is not None:
        try:
            return genesis.get_step_tier(None)
        except Exception:
            return 1
    return 1


def _verdict_decision(*, effective_tier: int, can_close: bool, active_cmt: str,
                      actor: str, workspace: str, ts: str) -> Decision:
    """The legacy pass-side verdict: Tier 3 defers to a human (no ledger
    write), otherwise submit-for-review or auto-close per the actor's
    close permission. The op is returned as an annotation for the
    adapter to persist."""
    if effective_tier >= 3:
        # Tier 3: leave in_review for human approval
        return Decision(verb=Verb.ALLOW, reason="Tier 3: awaiting human approval.")
    if not can_close:
        # Actor lacks close permission — submit for review instead
        submit_op = _ledger_op("submit", ts, active_cmt, actor, workspace, {
            "body": "Auto-submitted: all checks passed, awaiting reviewer close",
            "kind": "verdict",
            "commitment": active_cmt,
            "evidence": f"tier_{effective_tier}_auto"
        })
        return Decision(verb=Verb.ALLOW, annotate={"kind": "ledger", "body": submit_op})
    # Tier 1/2 + actor has close permission: auto-close
    close_op = _ledger_op("close", ts, active_cmt, actor, workspace, {
        "body": "Auto-closed: all checks passed",
        "kind": "verdict",
        "commitment": active_cmt,
        "evidence": f"tier_{effective_tier}_auto"
    })
    return Decision(verb=Verb.ALLOW, annotate={"kind": "ledger", "body": close_op})


@_fail_open
def gate_review(event: AgentEvent, *, ctx=None) -> Decision:
    """The Stop pipeline. Phases: commitment-closure, epistemic-loop,
    dual-triad, structural — then the ledger-native verdict, returned as
    an annotation instead of written.

    ctx keys (all optional; the adapter maps env/files into them):
      probes, protocol_state, stop_hook_active, active_commitment,
      commitment_state, tier, scope, workspace, genesis, claude_md_text,
      ledger_path, context_docs
    """
    ctx = ctx if isinstance(ctx, dict) else {}
    probes = ctx.get("probes")
    actor = event.actor
    workspace = ctx.get("workspace", "subtrace")
    genesis = ctx.get("genesis")

    active_cmt = ctx.get("active_commitment") or ""
    ledger_path = ctx.get("ledger_path") or ".mentu/ledger.jsonl"
    ledger_lines = probes.read_ledger_lines(ledger_path) if probes is not None else None
    ledger_exists = ledger_lines is not None

    def can_close() -> bool:
        if genesis is None:
            return True  # ungoverned ⇒ permissive (legacy GenesisReader default)
        return genesis.actor_allowed(actor, "close")

    # Infinite loop guard — always allow on second attempt
    # But still emit the verdict op (the first pass validated, agent fixed issues)
    if ctx.get("stop_hook_active", False):
        if active_cmt.startswith("cmt_") and ledger_exists:
            effective_tier = _effective_tier(ctx)
            ts = probes.now().strftime("%Y-%m-%dT%H:%M:%SZ")
            return _verdict_decision(
                effective_tier=effective_tier, can_close=can_close(),
                active_cmt=active_cmt, actor=actor, workspace=workspace, ts=ts)
        return Decision.allow()

    # Check if auto_review is enabled
    state = ctx.get("protocol_state")
    if not isinstance(state, dict) or not state.get("auto_review", False):
        return Decision.pass_()

    message = event.message or ""
    step_label = state.get("step_label", "")
    is_recon = "recon" in step_label.lower() if step_label else False

    # Run checks
    failures = []

    # ── Phase 1: Commitment Closure Check ──
    # The substrate status call lives in the adapter; ctx["commitment_state"]
    # is None when the substrate is absent (self-skip, like the legacy
    # is_available() guard).
    cmt_state = ctx.get("commitment_state")
    if active_cmt.startswith("cmt_") and cmt_state is not None:
        if cmt_state not in ("closed", "in_review"):
            failures.append(
                f"COMMITMENT: Active commitment `{active_cmt}` is in state `{cmt_state}`. "
                f"Call `mentu submit {active_cmt} --summary '...'` before stopping."
            )

    # ── Phase 1b: Epistemic Loop Detection (ledger-based) ──
    if ledger_exists and active_cmt:
        try:
            submits = []
            for line in ledger_lines:
                op = json.loads(line)
                if op.get("op") == "submit" and op.get("payload", {}).get("commitment") == active_cmt:
                    submits.append(op)
            if len(submits) >= 2:
                ev1 = submits[-1].get("payload", {}).get("evidence", [])
                ev2 = submits[-2].get("payload", {}).get("evidence", [])
                if ev1 == ev2:
                    failures.append(
                        "EPISTEMIC LOOP: Duplicate evidence submitted to same commitment. "
                        "Change approach or escalate."
                    )
        except Exception:
            pass

    # ── Phase 2: Dual Triad Validation ──
    effective_tier = _effective_tier(ctx)

    # Tag-based tier classification from genesis (overrides default when tags available)
    if active_cmt and ledger_exists and genesis is not None and getattr(genesis, "exists", False):
        try:
            tags = []
            for line in ledger_lines:
                entry = json.loads(line)
                if entry.get("payload", {}).get("commitment") == active_cmt:
                    domain = (entry.get("semantic") or {}).get("domain", [])
                    tags.extend(domain)
            if tags:
                tier_name = genesis.classify_tier(tags)
                try:
                    effective_tier = int(tier_name.split("_")[1])
                except (IndexError, ValueError):
                    pass
        except Exception:
            pass

    # Legacy genesis.scope is hardcoded ["*"] (inert); an explicit
    # ctx["scope"] is the only way to tighten it.
    scope = ctx.get("scope") or ["*"]
    triad = run_dual_triad(
        tier=effective_tier,
        scope=scope,
        last_message=message,
        probes=probes,
        build_cmd=read_build_cmd(ctx.get("claude_md_text")),
    )

    for result in triad.failures:
        failures.append(f"TRIAD/{result.validator.upper()}: {result.evidence} — {'; '.join(result.details)}")

    # ── Phase 3: Structural Checks (legacy, preserved) ──
    # Build is handled by the Dual Triad Technical validator, skip duplicate
    # but keep LOOP_COMPLETE as standalone check (it's also the Intent validator input)

    loop_err = check_loop_complete(message)
    # Only add if not already caught by Intent validator (tier 3)
    if loop_err and effective_tier < 3:
        failures.append(f"COMPLETION: {loop_err}")

    # Skip git-changes check for recon steps — they may only write
    # summaries to .claude/summaries/ or docs that aren't yet tracked
    if not is_recon:
        git_err = check_git_changes(probes)
        if git_err:
            failures.append(f"CHANGES: {git_err}")

    # Check CONTEXT doc was updated (if one exists for this sequence)
    context_err = check_context_doc(step_label, ctx.get("context_docs"))
    if context_err:
        failures.append(f"CONTEXT: {context_err}")

    # ── Phase 4: Ledger-native approve/reopen ──
    # The review gate verdict IS a ledger operation — RETURNED as an
    # annotation; the adapter appends it.
    if failures:
        blocked_text = (
            "REVIEW GATE — blocked exit. Fix these issues:\n\n"
            + "\n".join(f"  - {f}" for f in failures)
            + "\n\nAddress the failures above, then try to exit again."
        )
        if active_cmt and ledger_exists:
            ts = probes.now().strftime("%Y-%m-%dT%H:%M:%SZ")
            # REOPEN: labeled negative with basis
            basis = [r.validator for r in triad.failures] if triad.failures else ["structural"]
            reason = "; ".join(failures[:3])  # Top 3 failures as reason
            reopen_op = _ledger_op("reopen", ts, active_cmt, actor, workspace, {
                "commitment": active_cmt,
                "reason": reason,
                "meta": {
                    "tier": effective_tier,
                    "basis": basis,
                    "failure_count": len(failures)
                }
            })
            return Decision(verb=Verb.DENY, reason=blocked_text,
                            annotate={"kind": "ledger", "body": reopen_op})
        return Decision(verb=Verb.DENY, reason=blocked_text)

    if active_cmt and ledger_exists:
        ts = probes.now().strftime("%Y-%m-%dT%H:%M:%SZ")
        return _verdict_decision(
            effective_tier=effective_tier, can_close=can_close(),
            active_cmt=active_cmt, actor=actor, workspace=workspace, ts=ts)
    return Decision.allow()


# ---------------------------------------------------------------------------
# SubagentStop gate — ported from hooks/context_isolation_gate.py
# ---------------------------------------------------------------------------

def check_message(message: str) -> Optional[str]:
    """Check message for raw data leakage. Returns reason string if blocked, None if OK."""
    lines = message.split("\n")
    line_count = len(lines)

    # Check 1: Excessive line count (summaries should be < 100 lines)
    if line_count > 200:
        return f"Message has {line_count} lines (limit: 200). Write to .claude/summaries/ instead."

    # Check 2: Hex dump patterns — e.g. "0x1a2b3c4d" repeated
    hex_pattern = re.compile(r"0x[0-9a-fA-F]{6,}")
    hex_matches = hex_pattern.findall(message)
    if len(hex_matches) > 15:
        return f"Message contains {len(hex_matches)} hex addresses. Write findings to filesystem."

    # Check 3: Raw hex byte sequences (e.g. "ff e0 3c 7a" patterns)
    hex_bytes_pattern = re.compile(r"(?:[0-9a-fA-F]{2}\s){8,}")
    if len(hex_bytes_pattern.findall(message)) > 5:
        return "Message contains raw hex byte dumps. Summarize or write to filesystem."

    # Check 4: Large inline JSON arrays (>10 elements)
    json_array_pattern = re.compile(r"\[(?:[^[\]]*,){10,}[^[\]]*\]")
    if json_array_pattern.search(message):
        return "Message contains large JSON arrays. Write structured data to filesystem."

    return None


@_fail_open
def gate_context_isolation(event: AgentEvent) -> Decision:
    """Context-window hygiene over a sub-agent's return message. The
    activation check (protocol flag / deployed skill) stays with the
    caller; this judges the message itself."""
    message = event.message or ""
    if not message:
        return Decision.pass_()

    reason = check_message(message)
    if reason:
        redirect = (
            f"BLOCKED by context-isolation-gate: {reason}\n\n"
            "The sub-agent must write its findings to a file under .claude/summaries/ "
            "and return ONLY the file path. Raw data must not leak into the orchestrator's "
            "context window. See .claude/skills/context-isolation-protocol/SKILL.md for details."
        )
        return Decision(verb=Verb.DENY, reason=redirect,
                        annotate={"kind": "isolation_redirect", "body": reason})

    return Decision.allow()


# ---------------------------------------------------------------------------
# PreToolUse gate — ported from hooks/pre-tool-use-permission.sh
# ---------------------------------------------------------------------------

# Destructive tool patterns — these need higher trust to auto-allow
_DESTRUCTIVE_BASH = re.compile(r"(\brm\b|\bkill\b|\bdrop\b|--force|reset --hard|push --force|clean -f)")
_DESTRUCTIVE_WRITE = re.compile(r"(\.env|credentials|\.ssh|\.gnupg|settings\.json)")


def trust_from_ledger(lines: Optional[List[str]]) -> float:
    """Trust score from ledger: approve / (approve + warn + block).
    Mirrors the shell script's three `grep -c` marker counts and bc's
    scale=2 truncating division. Default 0.50 (neutral) when no ledger
    or no relevant entries."""
    if lines is None:
        return 0.50
    approve = sum(1 for line in lines if '"op":"approve"' in line)
    warn = sum(1 for line in lines if '"kind":"warning"' in line)
    block = sum(1 for line in lines if "BLOCKED" in line)
    total = approve + warn + block
    if total == 0:
        return 0.50
    return (approve * 100 // total) / 100


def _fmt_trust(trust: float) -> str:
    """bc prints scale=2 results without a leading zero (".90", "1.00");
    keep the same shape so reasons match the legacy strings."""
    s = "%.2f" % trust
    return s[1:] if s.startswith("0.") else s


@_fail_open
def gate_tool_permission(event: AgentEvent, *, trust: float) -> Decision:
    """Trust-banded tool permission: >=0.80 allow / <0.40 deny / middle
    band pass-through; destructive patterns raise the allow bar to 0.90."""
    tool = event.tool.name if event.tool else ""
    tool_input = event.tool.input if event.tool else {}

    is_destructive = False
    if tool == "Bash":
        if _DESTRUCTIVE_BASH.search(tool_input.get("command", "") or ""):
            is_destructive = True
    elif tool == "Write":
        if _DESTRUCTIVE_WRITE.search(tool_input.get("file_path", "") or ""):
            is_destructive = True

    allow_threshold = "0.90" if is_destructive else "0.80"
    deny_threshold = "0.40"

    if trust >= float(allow_threshold):
        return Decision.allow(
            f"CIR trust above threshold ({_fmt_trust(trust)} >= {allow_threshold})")
    if trust < float(deny_threshold):
        return Decision.deny(
            f"CIR trust below threshold ({_fmt_trust(trust)} < {deny_threshold})")
    # Middle range: no decision, fall through to the agent's default behavior
    return Decision.pass_()


# ---------------------------------------------------------------------------
# Engine — routes gate events; registered into core.evaluate's dispatch
# ---------------------------------------------------------------------------

def gate_engine(event, ctx) -> Decision:
    """Gate engine for core.evaluate: stop -> review pipeline,
    subagent_stop -> context isolation (when ctx activates it),
    pre_tool / permission_request -> trust-banded permission.

    With no ctx (no probes, no activation flags) every path is a
    deterministic PASS — the harness without its adapter has no opinion.
    """
    try:
        ctx_dict = ctx if isinstance(ctx, dict) else {}
        kind = event.event
        if kind == "stop":
            return gate_review(event, ctx=ctx_dict)
        if kind == "subagent_stop":
            # Activation (protocol flag / deployed skill) is envelope state
            # the adapter resolves; inactive == the legacy early exit 0.
            if not ctx_dict.get("isolation_active", False):
                return Decision.pass_()
            return gate_context_isolation(event)
        if kind in ("pre_tool", "permission_request"):
            if "trust" in ctx_dict:
                trust = ctx_dict["trust"]
            else:
                probes = ctx_dict.get("probes")
                ledger_path = ctx_dict.get("ledger_path") or ".mentu/ledger.jsonl"
                lines = probes.read_ledger_lines(ledger_path) if probes is not None else None
                trust = trust_from_ledger(lines)
            return gate_tool_permission(event, trust=trust)
        return Decision.pass_()
    except BaseException:
        return Decision.pass_()


core.register_gate_engine(gate_engine)
