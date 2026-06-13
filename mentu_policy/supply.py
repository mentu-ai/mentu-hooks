#!/usr/bin/env python3
"""mentu_policy.supply — the context-supply engine (M2b).

``supply_context(event, ctx) -> Decision`` unifies the four read-only,
additive context-supply sources the legacy repo ran as separate Claude-Code
hooks. It NEVER mutates the substrate (the recording-fake test in
``tests/test_supply_observe.py`` proves zero mutating calls) and degrades to
``Decision.pass_()`` whenever the substrate is absent.

  * ``session_start`` — the CIR brief of ``cir_session_context.py`` (file 1:
    crystallized patterns + recent signals) PLUS the READ-ONLY lifecycle brief
    of ``mentu_session_start.py`` (file 4: active commitment, Genesis role /
    permissions, claimed commitments). The MUTATING lifecycle logic of file 4
    — stale-commitment close, ensure-commitment, ``sync()``, and the
    ``CLAUDE_ENV_FILE`` export — is intentionally NOT ported; it stays in the
    legacy hook.
  * ``post_compact`` — the protocol-state re-seed of ``compaction_reinjector.py``
    (file 3), read from ``<cwd>/.claude/pre-compact-state.json``.
  * ``pre_tool`` (tool ``Agent``) — the sub-agent prompt enrichment of
    ``pre-tool-use-inject.sh`` (file 2): the original prompt + a read-only CIR
    ledger block + trust state, returned via ``updated_input.prompt``.

**Substrate is reached only through ``ctx["substrate"]``** — the same
injectable-seam discipline the gate engine uses for ``probes``. ``evaluate``
calls the engine with ``ctx=None``, so without an adapter wiring a substrate
in, every supply event is a deterministic PASS — the harness without its
adapter has no opinion. The Claude adapter (M3) constructs a ``Substrate`` and
puts it (plus the workspace ``cwd``) into ``ctx``.

The workspace-relative reads (``.mentu/active_commitment``,
``.mentu/ledger.jsonl``, ``.mentu/genesis.json``, ``.claude/pre-compact-state.json``)
are keyed off ``ctx["cwd"]`` / ``event.cwd`` — never the process CWD — so the
engine is pure and hermetic under test.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .abi import AgentEvent, Decision
from .genesis import GenesisReader


# ---------------------------------------------------------------------------
# File 1 — CIR brief (ported from cir_session_context.py:44-79)
# ---------------------------------------------------------------------------

def render_cir_block(patterns, signals) -> str:
    """Render the CIR patterns + recent-evidence markdown. Empty string when
    both are empty (the legacy 'exit silently' path)."""
    if not signals and not patterns:
        return ""
    output = []
    if patterns:
        output.append("## CIR Patterns (compound learning)")
        output.append("")
        for p in patterns[:5]:
            name = p.get("name", p.get("id", "?"))
            count = p.get("recurrenceCount", p.get("recurrence_count", 0))
            strength = p.get("strength", 0)
            desc = p.get("description", "")
            line = f"- **{name}** (seen {count}x, strength: {strength:.0%})"
            if desc:
                line += f" — {desc}"
            output.append(line)
        output.append("")
    if signals:
        output.append("## Recent CIR Evidence")
        output.append("")
        for s in signals[:5]:
            conf = s.get("effectiveConfidence", s.get("effective_confidence"))
            conf_str = f"{conf:.0%}" if isinstance(conf, (int, float)) else "?"
            body = str(s.get("body", ""))[:100]
            ts = str(s.get("ts", "?"))[:10]
            output.append(f"- [{ts}] {body} (confidence: {conf_str})")
        output.append("")
    return "\n".join(output)


# ---------------------------------------------------------------------------
# File 4 — read-only lifecycle brief (ported from mentu_session_start.py;
# mutating lifecycle / env-export deliberately omitted)
# ---------------------------------------------------------------------------

_TIER_DESC = {1: "(technical only)", 2: "(technical + safety)",
              3: "(technical + safety + intent)"}


def _read_active_commitment(root: str) -> str:
    """Active commitment id from ``<root>/.mentu/active_commitment`` (read)."""
    try:
        p = Path(root) / ".mentu" / "active_commitment"
        if p.exists():
            cmt = p.read_text().strip()
            if cmt.startswith("cmt_"):
                return cmt
    except OSError:
        pass
    return ""


def render_lifecycle_block(root: str, actor: str, substrate) -> str:
    """Render the READ-ONLY '## Mentu Lifecycle State' brief. Socket reads
    (claimed commitments) happen only when the socket is reachable; nothing
    here mutates. Empty string when there is nothing governed to say."""
    active_cmt = _read_active_commitment(root)
    genesis = GenesisReader(workspace_dir=root)

    claimed = []
    try:
        if substrate is not None and substrate.socket_available():
            for c in substrate.list_commitments(state="claimed"):
                if isinstance(c, dict) and c.get("owner") == actor:
                    claimed.append(c)
    except Exception:
        claimed = []

    if not (active_cmt or claimed or genesis.exists):
        return ""

    effective_tier = genesis.get_step_tier(None)
    parts = ["## Mentu Lifecycle State", ""]

    if active_cmt:
        parts.append(f"**Active commitment (in-progress):** `{active_cmt}`")
        parts.append(f"**Validation tier:** {effective_tier} {_TIER_DESC.get(effective_tier, '')}".rstrip())
        parts.append("")

    parts.append(genesis.format_context(active_cmt))

    if genesis.exists:
        role = genesis.resolve_role(actor)
        allowed = genesis.get_allowed_ops(actor)
        denied = genesis.get_denied_ops(actor)
        allowed_str = ", ".join(allowed) if allowed else "none"
        denied_str = ", ".join(denied) if denied else "none"
        parts.append(f"Your role: {role}. You can: {allowed_str}. You CANNOT: {denied_str}.")

    if claimed:
        parts.append("")
        parts.append("**Claimed commitments** — you MUST submit each before stopping:")
        parts.append("")
        for cmt in claimed:
            parts.append(f"- `{cmt.get('id')}`: {cmt.get('body')}")

    return "\n".join(parts)


def render_session_brief(event: AgentEvent, root: str, substrate) -> Optional[str]:
    """Files 1 + 4 unified: the CIR brief + the read-only lifecycle brief.
    Returns the combined markdown, or None when there is nothing to inject."""
    blocks = []

    patterns = signals = None
    try:
        patterns = substrate.cir_patterns()
        signals = substrate.cir_query(limit=5)
    except Exception:
        patterns = signals = None
    cir = render_cir_block(patterns, signals)
    if cir.strip():
        blocks.append(cir.rstrip())

    lifecycle = render_lifecycle_block(root, event.actor, substrate)
    if lifecycle.strip():
        blocks.append(lifecycle.rstrip())

    text = "\n\n".join(blocks).strip()
    return text or None


# ---------------------------------------------------------------------------
# File 3 — post-compaction re-seed (ported from compaction_reinjector.py:29-83)
# ---------------------------------------------------------------------------

def render_compact_brief(root: str) -> Optional[str]:
    """Protocol-state brief from ``<root>/.claude/pre-compact-state.json``.
    None on any absent/unreadable/empty-protocols path (the legacy ``exit 0``
    branches)."""
    try:
        state_file = Path(root) / ".claude" / "pre-compact-state.json"
        if not state_file.exists():
            return None
        state = json.loads(state_file.read_text())
    except (json.JSONDecodeError, OSError, ValueError):
        return None
    if not isinstance(state, dict):
        return None

    protocols = state.get("active_protocols", [])
    if not protocols:
        return None

    context = "## Protocol State (restored after compaction)\n\n"
    context += f"**Active protocols:** {', '.join(protocols)}\n"

    step_label = state.get("step_label", "")
    if step_label:
        context += f"**Current step:** {step_label}\n"

    seq_name = state.get("sequence_name", "")
    if seq_name:
        context += f"**Sequence:** {seq_name}\n"

    context += "\n"

    reminders = state.get("reminders", [])
    if reminders:
        context += "**IMPORTANT reminders:**\n"
        for r in reminders:
            context += f"- {r}\n"
        context += "\n"

    for key, val in state.items():
        if key.endswith("_gate") and isinstance(val, dict):
            recon = val.get("recon_artifact", "")
            if recon:
                context += f"**Recon artifact ({key}):** `{recon}`"
                # Path(root) / <abs recon> collapses to <abs recon>, so this
                # honors both relative and absolute recon paths.
                if (Path(root) / recon).exists():
                    context += " (exists on disk)"
                else:
                    context += " (MISSING)"
                context += "\n"

    if state.get("auto_review"):
        context += "\n**Auto-review is ON** — your exit will be gated on: build pass, LOOP_COMPLETE marker, and git changes.\n"

    return context


# ---------------------------------------------------------------------------
# File 2 — sub-agent prompt enrichment (ported from pre-tool-use-inject.sh)
# ---------------------------------------------------------------------------

def render_ledger_block(root: str) -> str:
    """The CIR-context + trust-state block appended to a sub-agent prompt.
    Read-only over ``<root>/.mentu/ledger.jsonl``; empty string when there is
    no ledger or nothing recent to surface (the legacy ``echo '{}'`` path)."""
    ledger = Path(root) / ".mentu" / "ledger.jsonl"
    try:
        if not ledger.exists():
            return ""
        lines = ledger.read_text().splitlines()
    except OSError:
        return ""

    # CIR context: tail -20, select op capture/annotate, format, tail -10.
    recent = []
    for line in lines[-20:]:
        try:
            op = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(op, dict):
            continue
        if op.get("op") in ("capture", "annotate"):
            payload = op.get("payload") if isinstance(op.get("payload"), dict) else {}
            body = payload.get("body") or op.get("body") or op.get("kind") or "unknown"
            recent.append(f"{op.get('op')}: {body}")
    recent = recent[-10:]

    cir_context = ""
    if recent:
        cir_context = (
            "\n\n<cir-context>\n"
            "Recent evidence from the epistemic ledger (read-only — do not modify):\n"
            + "\n".join(recent)
            + "\n</cir-context>"
        )

    # Trust chain summary: grep-equivalent marker counts.
    approve = sum(1 for ln in lines if '"op":"approve"' in ln)
    warn = sum(1 for ln in lines if '"kind":"warning"' in ln)
    trust_context = ""
    if approve > 0 or warn > 0:
        trust_context = f"\nTrust state: {approve} approvals, {warn} warnings in this session."

    return cir_context + trust_context


# ---------------------------------------------------------------------------
# Engine — unifies files 1/2/3/4; registered into core.evaluate's dispatch
# ---------------------------------------------------------------------------

def _workspace_root(event, ctx: dict) -> str:
    return ctx.get("cwd") or getattr(event, "cwd", "") or "."


def supply_context(event: AgentEvent, ctx=None) -> Decision:
    """AgentEvent -> Decision over the read-only context-supply tiers.

    Substrate-bearing events (``session_start``) require ``ctx["substrate"]``
    to be present and available; absent => PASS (fail-open). File-only events
    (``post_compact``, ``pre_tool`` Agent) read the workspace at
    ``ctx["cwd"]`` / ``event.cwd`` and PASS when there is nothing to supply.
    """
    ctx = ctx if isinstance(ctx, dict) else {}
    kind = getattr(event, "event", "")

    if kind == "session_start":
        substrate = ctx.get("substrate")
        if substrate is None or not substrate.available():
            return Decision.pass_()                       # fail-open
        root = _workspace_root(event, ctx)
        brief = render_session_brief(event, root, substrate)
        return Decision.supply(md=brief) if brief else Decision.pass_()

    if kind == "post_compact":
        if getattr(event, "source", None) != "compact":
            return Decision.pass_()
        root = _workspace_root(event, ctx)
        brief = render_compact_brief(root)
        return Decision.supply(md=brief) if brief else Decision.pass_()

    if kind == "pre_tool":
        tool = getattr(event, "tool", None)
        if tool is None or getattr(tool, "name", "") != "Agent":
            return Decision.pass_()
        tool_input = getattr(tool, "input", None)
        original = tool_input.get("prompt", "") if isinstance(tool_input, dict) else ""
        original = original or ""
        if not original:
            return Decision.pass_()
        root = _workspace_root(event, ctx)
        block = render_ledger_block(root)
        if not block:
            return Decision.pass_()
        return Decision.supply(prompt=original + block)

    return Decision.pass_()


def supply_engine(event, ctx) -> Decision:
    """Adapter for ``core.evaluate``'s dispatch (session_start / post_compact /
    prompt_submit). Fail-open: any internal fault degrades to PASS."""
    try:
        return supply_context(event, ctx)
    except BaseException:
        return Decision.pass_()
