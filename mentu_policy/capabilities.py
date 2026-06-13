#!/usr/bin/env python3
"""mentu_policy.capabilities — per-agent enforcement capability registry (M1).

The single source of truth the adapters and the installer consult before a
decision is enforced. Derived from the audit's capability matrix
(BUILD-Mentu-Policy-Harness-v1.0 Appendix C). `gate: False` for Gemini is
the load-bearing honesty: its lifecycle events are post-hoc, so a deny
cannot prevent the action — the adapter degrades it (M4). "partial" marks
a constrained channel (e.g. context supplied only by prepending to a
prompt, not via a structured field).
"""

# tier ∈ {observe, supply_context, gate, compaction}
CAPABILITIES = {
    "claude": {"observe": True,  "supply_context": True,  "gate": True,  "compaction": True},
    "codex":  {"observe": True,  "supply_context": "partial", "gate": True,  "compaction": False},
    "cursor": {"observe": True,  "supply_context": "partial", "gate": True,  "compaction": False},
    "gemini": {"observe": True,  "supply_context": "partial", "gate": False, "compaction": False},
    "mentu":  {"observe": True,  "supply_context": True,  "gate": True,  "compaction": True},
}


def supports(agent: str, tier: str) -> bool:
    return CAPABILITIES.get(agent, {}).get(tier, False) is True
