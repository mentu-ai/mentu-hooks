#!/usr/bin/env python3
"""mentu_policy.degrade — the capability degradation ladder (M4).

``apply_capability(decision, agent, event) -> (applied_decision, signal_or_none)``

A ``Decision`` is computed by policy-core *without regard* to which agent will
enforce it (``core.evaluate`` only sees normalized facts). This ladder is the
honest reconciliation between that verdict and what the target agent can
actually do — read from ``capabilities.CAPABILITIES`` — applied in the adapter
shim between ``evaluate`` and ``encode``:

  * ``deny`` / ``ask`` on a NON-GATING agent (Gemini: its lifecycle events are
    post-hoc, so a refusal cannot prevent the action) cannot be enforced.
    DOWN-SHIFT to ``annotate`` (observe + warn): the encoder then emits the
    agent's additive no-op, never a false "blocked" claim. One
    ``capability_degraded`` signal ``{agent, requested_verb, applied_verb,
    reason}`` records the inability so it is auditable, never silently dropped.

  * ``inject`` on a CONSTRAINED (``"partial"``) channel has no structured
    ``additionalContext`` field — RE-ENCODE the context as a prompt prefix (it
    is still delivered, just through the prompt). No signal: nothing was lost.
    ``inject`` where NO supply channel exists at all → SKIP (``pass``) + a
    ``supply_skipped`` signal.

  * a FULL-capability agent (``gate`` / ``supply_context`` is ``True``) passes
    through unchanged, no signal. An UNKNOWN agent fails open the same way —
    the ladder never invents a capability claim it cannot ground in the
    registry, and never raises.

The ladder is PURE: it returns the (possibly down-shifted) ``Decision`` and an
optional signal dict; the SHIM fires the signal through the same
fire-and-forget ``Substrate.capture_signal`` path the lifecycle capture uses,
so a slow or absent substrate never blocks the agent. Reference:
BUILD-Mentu-Policy-Harness-v1.0 — "Capability matrix + graceful degradation
ladder" + Appendix C.
"""
from __future__ import annotations

from typing import Optional, Tuple

from . import capabilities
from .abi import Decision, Verb


def _signal(kind: str, agent: str, requested_verb: str, applied_verb: str,
            reason: str) -> dict:
    """The audit signal a down-shift emits. Carries the signal ``kind``
    (``capability_degraded`` | ``supply_skipped``) plus the four descriptive
    fields the spec pins: ``{agent, requested_verb, applied_verb, reason}``."""
    return {
        "kind": kind,
        "agent": agent,
        "requested_verb": requested_verb,
        "applied_verb": applied_verb,
        "reason": reason or "",
    }


def _prompt_prefix(decision: Decision) -> str:
    """The prompt-prepend payload for a constrained inject: the structured
    context (``inject_context``) prepended to any prompt the decision already
    carries (the Agent-enrichment shape already lives in
    ``updated_input.prompt``)."""
    md = decision.inject_context or ""
    existing = ""
    if isinstance(decision.updated_input, dict):
        existing = decision.updated_input.get("prompt") or ""
    if md and existing:
        return md + "\n\n" + existing
    return md or existing


def apply_capability(decision, agent, event) -> Tuple[Optional[Decision], Optional[dict]]:
    """Reconcile a ``Decision`` against ``agent``'s enforcement capability.

    Returns ``(applied_decision, signal_or_none)``. Never raises: a non-Decision
    input or an unknown agent fails open (pass the decision through, no signal).
    """
    if not isinstance(decision, Decision):
        return decision, None

    row = capabilities.CAPABILITIES.get(agent)
    if row is None:
        # Unknown agent: no registry row to ground a capability claim → fail
        # open. The adapter (if any) encodes the decision as-is.
        return decision, None

    verb = decision.verb

    # ── gate ladder: deny / ask on a non-gating agent → annotate (never block) ──
    if verb in (Verb.DENY, Verb.ASK) and row.get("gate") is not True:
        reason = decision.reason or ""
        applied = Decision(
            verb=Verb.ANNOTATE, reason=reason,
            annotate={"kind": "capability_degraded",
                      "body": "%s requested but %s cannot gate (post-hoc)"
                              % (verb.value, agent)},
        )
        return applied, _signal("capability_degraded", agent, verb.value,
                                "annotate", reason)

    # ── supply ladder: inject on a non-full channel ──
    if verb == Verb.INJECT and row.get("supply_context") is not True:
        if row.get("supply_context") == "partial":
            prompt = _prompt_prefix(decision)
            if prompt:
                # Re-encode as a prompt prefix — the context is still delivered,
                # just through the prompt. Not a loss → no signal.
                return Decision(verb=Verb.INJECT,
                                updated_input={"prompt": prompt}), None
            # partial but nothing to prepend → fall through to skip.
        # No channel at all (or nothing to prepend) → skip + supply_skipped.
        return Decision.pass_(), _signal("supply_skipped", agent, "inject",
                                         "skip", decision.reason or "")

    # Full capability (or a verb the agent can already honor) → unchanged.
    return decision, None
