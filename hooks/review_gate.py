#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Review Gate — Stop hook with Commitment Protocol + Dual Triad enforcement.

Three-phase gate:
  Phase 1: Commitment closure — active commitment must be submitted/closed
  Phase 2: Dual Triad validation — Technical + Safety + Intent (tiered)
  Phase 3: Structural checks — build, LOOP_COMPLETE, git changes (legacy)

Respects stop_hook_active to prevent infinite loops — always allows exit on
second attempt.

Activates when either:
  - protocol-state.json has "auto_review": true (legacy)
  - .mentu/active_commitment exists (commitment protocol)

Exit codes:
  0 = allow exit
  2 = block exit with feedback

Delegation: the verdict is computed by the mentu_policy claude adapter stop
pipeline (one decision authority, golden-parity gated). The pipeline returns the
ledger verdict as an annotation; THIS hook is where that annotation lands in
.mentu/ledger.jsonl (policy-core stays pure and replayable — it never writes the
ledger). Fails open (exit 0) on any fault.
"""
from __future__ import annotations

import sys
from pathlib import Path


def _bootstrap_policy_path() -> None:
    """Make ``mentu_policy`` importable relative to THIS file (repo:
    hooks/../mentu_policy; a future deployed copy: alongside in hooks/)."""
    here = Path(__file__).resolve().parent
    for cand in (here.parent, here):
        if (cand / "mentu_policy" / "__init__.py").exists():
            p = str(cand)
            if p not in sys.path:
                sys.path.insert(0, p)
            return


def _persist_ledger_annotation(decision) -> None:
    """Append a returned ``annotate{kind:"ledger"}`` verdict op to
    .mentu/ledger.jsonl (the gate returns it; the hook persists it)."""
    ann = getattr(decision, "annotate", None)
    if not isinstance(ann, dict) or ann.get("kind") != "ledger":
        return
    body = ann.get("body")
    if not body:
        return
    try:
        with open(Path(".mentu") / "ledger.jsonl", "a") as f:
            f.write(body + "\n")
    except OSError:
        pass


def main():
    stdin_text = sys.stdin.read()
    _bootstrap_policy_path()
    from mentu_policy.adapters import shim
    stdout, code, decision = shim.run_with_decision("claude", "stop", stdin_text)
    _persist_ledger_annotation(decision)
    if stdout:
        sys.stdout.write(stdout)
    sys.exit(code)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        sys.stderr.write(f"review_gate error: {e}\n")
        sys.exit(0)  # Fail open
