#!/usr/bin/env bash
# capture-golden-vectors.sh — pin the legacy hooks' EXACT outputs as golden
# vectors, the behavior-preservation baseline for the Mentu Policy Harness
# (BUILD-Mentu-Policy-Harness-v1.0 §M2a / Appendix B).
#
# This is the regenerable baseline taken BEFORE M3 rewires hooks/. It only
# READS/executes verbatim SNAPSHOTS of the legacy scripts inside throwaway
# mktemp sandboxes (never the real ~, never hooks/). The decision logic is
# stdlib-only Python in tests/sandbox.py; this script is the entry point.
#
#   capture (default) : freeze hooks/ -> fixtures/legacy/, run every case,
#                       write fixtures/golden/<case>.golden.json + native input
#   --verify          : re-run every case against the COMMITTED legacy snapshot
#                       and diff the goldens; exit non-zero on any mismatch
#
# Per case the record is {exit_code, stdout, mentu_argv[], ledger_ops_normalized[]}.
# Only the review-gate ledger nondeterminism is normalized (ts/id zeroed).
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
HOOKS_REPO=$(cd "$SCRIPT_DIR/.." && pwd)
export HOOKS_DIR="$HOOKS_REPO/hooks"
export TESTS_DIR="$HOOKS_REPO/tests"
FIXTURES="$TESTS_DIR/fixtures"
export LEGACY="$FIXTURES/legacy"
export GOLDEN="$FIXTURES/golden"
export NATIVE="$FIXTURES/native"

MODE="${1:-capture}"

exec python3 - "$MODE" <<'PYEOF'
from __future__ import annotations
import json
import os
import shutil
import sys

TESTS_DIR = os.environ["TESTS_DIR"]
HOOKS_DIR = os.environ["HOOKS_DIR"]
LEGACY = os.environ["LEGACY"]
GOLDEN = os.environ["GOLDEN"]
NATIVE = os.environ["NATIVE"]

sys.path.insert(0, TESTS_DIR)
import sandbox  # noqa: E402

MODE = sys.argv[1] if len(sys.argv) > 1 else "capture"
VERIFY = (MODE == "--verify")

# Hooks resolve helper tools through the sandbox PATH (everything but the
# mentu/timeout stubs comes from the system dirs); mirror that path here so a
# missing jq/bc/git skips the dependent case instead of crashing it.
SANDBOX_TOOLPATH = "/usr/bin:/bin:/usr/sbin:/sbin"


def have(tool: str) -> bool:
    return shutil.which(tool, path=SANDBOX_TOOLPATH) is not None


def skip_reason(case: dict):
    for t in case.get("requires", []):
        if not have(t):
            return "missing tool: %s" % t
    snap = os.path.join(LEGACY, case["script"])
    if not os.path.exists(snap):
        return "legacy snapshot absent: %s" % case["script"]
    return None


def short_diff(want: dict, got: dict) -> str:
    out = []
    keys = sorted(set(list(want.keys()) + list(got.keys())))
    for k in keys:
        if want.get(k) != got.get(k):
            wv = json.dumps(want.get(k), ensure_ascii=True)[:240]
            gv = json.dumps(got.get(k), ensure_ascii=True)[:240]
            out.append("      %s:\n        want=%s\n        got =%s" % (k, wv, gv))
    return "\n".join(out)


# ── snapshot the legacy scripts verbatim (capture only) ──
if not VERIFY:
    os.makedirs(LEGACY, exist_ok=True)
    os.makedirs(GOLDEN, exist_ok=True)
    os.makedirs(NATIVE, exist_ok=True)
    snapped = 0
    for fn in sandbox.SNAPSHOT_SCRIPTS:
        src = os.path.join(HOOKS_DIR, fn)
        if not os.path.exists(src):
            print("  WARN cannot snapshot (source missing): %s" % fn)
            continue
        shutil.copy2(src, os.path.join(LEGACY, fn))
        snapped += 1
    print("snapshot: froze %d/%d legacy scripts -> fixtures/legacy/"
          % (snapped, len(sandbox.SNAPSHOT_SCRIPTS)))

cases = sandbox.golden_cases()
captured = 0
skipped = []
mismatches = []
checked = 0

for case in cases:
    name = case["name"]
    reason = skip_reason(case)
    if reason:
        skipped.append((name, reason))
        print("  SKIP %-36s (%s)" % (name, reason))
        continue

    rec = sandbox.run_case(case, LEGACY)
    gpath = os.path.join(GOLDEN, name + ".golden.json")

    if VERIFY:
        checked += 1
        if not os.path.exists(gpath):
            mismatches.append((name, "golden file missing"))
            print("  MISS %s" % name)
            continue
        with open(gpath) as f:
            want = json.load(f)
        if want != rec:
            mismatches.append((name, "record differs"))
            print("  DIFF %s" % name)
            print(short_diff(want, rec))
        else:
            print("  ok   %s" % name)
    else:
        with open(gpath, "w") as f:
            f.write(json.dumps(rec, indent=2, sort_keys=True, ensure_ascii=True) + "\n")
        # Materialize the native input record alongside the golden.
        with open(os.path.join(NATIVE, name + ".stdin"), "w") as f:
            f.write(case.get("stdin", ""))
        if case.get("argv"):
            with open(os.path.join(NATIVE, name + ".argv.json"), "w") as f:
                f.write(json.dumps(case["argv"]) + "\n")
        captured += 1
        print("  CAP  %-36s exit=%-3s argv=%d ops=%d"
              % (name, rec["exit_code"], len(rec["mentu_argv"]),
                 len(rec["ledger_ops_normalized"])))

print("")
if VERIFY:
    print("VERIFY: checked=%d  mismatches=%d  skipped=%d" % (checked, len(mismatches), len(skipped)))
    for n, why in mismatches:
        print("  MISMATCH %s: %s" % (n, why))
    sys.exit(1 if mismatches else 0)
else:
    print("CAPTURE: captured=%d  skipped=%d  total=%d" % (captured, len(skipped), len(cases)))
    for n, why in skipped:
        print("  skipped %s: %s" % (n, why))
    sys.exit(0)
PYEOF
