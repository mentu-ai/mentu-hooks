#!/usr/bin/env bash
# e2e-policy-harness.sh — Appendix B invariant harness for the Mentu Policy
# Harness (sibling of the top-level scripts/e2e-legibility.sh).
#
# Asserts the cross-cutting invariants of the cross-agent governance layer after
# every milestone, exercising the SHIPPED code paths (the generic shim, the
# rewired Claude python hooks + shell shims, the capability degradation ladder,
# the capability-aware installer, the golden-vector baseline):
#
#   1  fail-open with an absent substrate          7  cursor real permission verdict
#   2  fail-open on garbage stdin + py_compile      8  gemini capability honesty
#   3  staged-secret review-gate block + reopen     9  golden behavior-preservation
#   4  explicit-scope out-of-scope deny            10  actor-precedence matrix
#   5  context-isolation line boundary             11  installer idempotency (sandbox HOME)
#   6  trust-banded tool permission                12  one core, two agents
#
# GLOBAL ISOLATION: HOME + MENTU_HOME live under a throwaway ROOT, and a
# PATH-front argv-logging `mentu` stub stands in for the real binary. This
# harness NEVER reads or writes the real ~.
set -uo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
HOOKS_REPO=$(cd "$SCRIPT_DIR/.." && pwd)
PKG="$HOOKS_REPO/mentu_policy"
HOOKS="$HOOKS_REPO/hooks"
SHIM="$PKG/adapters/shim.py"
GOLDEN_SCRIPT="$HOOKS_REPO/scripts/capture-golden-vectors.sh"
INSTALLER="$HOOKS_REPO/scripts/install-agent-hooks.sh"
FIX="$HOOKS_REPO/tests/fixtures/sandbox"

ROOT=$(mktemp -d /tmp/e2e-policy.XXXXXX)

# ── Global isolation — every path under ROOT; the real home is never touched ──
export HOME="$ROOT/home"
export MENTU_HOME="$ROOT/home/.mentu"
export MENTU_STUB_LOG="$ROOT/home/mentu-argv.log"
mkdir -p "$HOME/.local/bin" "$MENTU_HOME"
# Strip actor-detection env so case 10 drives the precedence itself.
unset MENTU_ACTOR SUPERSET_TAB_ID CURSOR_SESSION_ID CODEX_SESSION_ID GEMINI_SESSION_ID 2>/dev/null || true

cat > "$HOME/.local/bin/mentu" <<'STUB'
#!/bin/bash
# argv-logging mentu stub: append argv as a JSON array, never reach a real CLI.
LOG="${MENTU_STUB_LOG:-$HOME/mentu-argv.log}"
/usr/bin/python3 -c 'import json,sys;print(json.dumps(sys.argv[1:]))' "$@" >> "$LOG"
exit 0
STUB
chmod +x "$HOME/.local/bin/mentu"
export PATH="$HOME/.local/bin:$PATH"

PASS=0; FAIL=0; SKIP=0
declare -a RESULTS
note(){ printf '\n\033[36m── %s ──\033[0m\n' "$1"; }
assert(){ if [ "$1" = "1" ]; then PASS=$((PASS+1)); printf '  \033[32m✅\033[0m %s\n' "$2"; RESULTS+=("PASS $2");
          else FAIL=$((FAIL+1)); printf '  \033[31m❌\033[0m %s\n' "$2"; RESULTS+=("FAIL $2"); fi; }
skip(){ SKIP=$((SKIP+1)); printf '  \033[33m∅\033[0m %s\n' "$1"; RESULTS+=("SKIP $1"); }

OUT=""; CODE=0
run_shim(){ # run_shim <cwd> <stdin> <shim-args...> -> sets OUT, CODE
  local cwd="$1" stdin="$2"; shift 2
  OUT="$(cd "$cwd" && printf '%s' "$stdin" | python3 "$SHIM" "$@" 2>/dev/null)"; CODE=$?
}
trimws(){ printf '%s' "$1" | tr -d '[:space:]'; }

# ───────────────────────── Pre-flight ─────────────────────────
note "Pre-flight"
{ [ -f "$SHIM" ] && [ -d "$PKG" ]; } && assert 1 "policy package + shim present" || { assert 0 "package/shim missing"; }
[ -x "$HOME/.local/bin/mentu" ] && assert 1 "PATH-front argv-logging mentu stub installed (HOME=$HOME)" || assert 0 "mentu stub missing"

# ───────── 1. Absent-substrate fail-open: 11 canonical events → claude ─────────
note "1. Absent-substrate fail-open (11 canonical events)"
EVENTS="session_start prompt_submit pre_tool post_tool post_tool_failure permission_request pre_compact post_compact subagent_stop stop session_end"
n=0; bad=0; denies=0
for e in $EVENTS; do
  n=$((n+1))
  run_shim "$ROOT" '{}' --agent claude --event "$e"
  [ "$CODE" = 0 ] || bad=$((bad+1))
  printf '%s' "$OUT" | grep -qi 'deny' && denies=$((denies+1))
done
{ [ "$n" = 11 ] && [ "$bad" = 0 ] && [ "$denies" = 0 ]; } \
  && assert 1 "11/11 events exit 0, 0 denies (empty substrate ⇒ no opinion)" \
  || assert 0 "fail-open broken (events=$n nonzero=$bad denies=$denies)"

# ───────── 2. Garbage-stdin fail-open on every entry hook + py_compile ─────────
note "2. Garbage-stdin fail-open (every entry hook) + py_compile"
GARBAGE='this is not valid json {{{ ]]'
g_total=0; g_bad=0
for hk in review_gate.py context_isolation_gate.py; do
  g_total=$((g_total+1))
  if ( cd "$ROOT" && printf '%s' "$GARBAGE" | python3 "$HOOKS/$hk" ) >/dev/null 2>&1; then :; else g_bad=$((g_bad+1)); fi
done
for hk in mentu_agent_hook.sh codex_cir_hook.sh gemini_cir_hook.sh pre-tool-use-permission.sh pre-tool-use-inject.sh; do
  g_total=$((g_total+1))
  if ( cd "$ROOT" && printf '%s' "$GARBAGE" | bash "$HOOKS/$hk" ) >/dev/null 2>&1; then :; else g_bad=$((g_bad+1)); fi
done
g_total=$((g_total+1))
if ( cd "$ROOT" && printf '%s' "$GARBAGE" | bash "$HOOKS/cursor_cir_hook.sh" beforeShellExecution ) >/dev/null 2>&1; then :; else g_bad=$((g_bad+1)); fi
[ "$g_bad" = 0 ] && assert 1 "$g_total/$g_total entry hooks exit 0 on garbage stdin" || assert 0 "$g_bad/$g_total entry hooks broke on garbage"

if find "$PKG" -name '*.py' -not -path '*/__pycache__/*' -print0 | xargs -0 python3 -m py_compile 2>/dev/null \
   && python3 -m py_compile "$HOOKS/review_gate.py" "$HOOKS/context_isolation_gate.py" 2>/dev/null; then
  assert 1 "py_compile clean over mentu_policy package + rewired python hooks"
else
  assert 0 "py_compile failed over the package / rewired hooks"
fi

# ───────── 3. Synthetic staged secret → review_gate exits 2 + 1 reopen ─────────
note "3. Staged secret → review_gate.py blocks (exit 2) + one reopen op"
if command -v git >/dev/null 2>&1; then
  ws="$ROOT/secret-ws"; mkdir -p "$ws/.mentu" "$ws/.claude"
  ( cd "$ws"
    git init -q
    git config user.email e2e@m; git config user.name e2e
    printf '# Repo\n\n## Commands\n\n```bash\necho build ok\n```\n' > CLAUDE.md
    printf 'base\n' > app.py
    git add -A && git commit -qm base >/dev/null 2>&1
    # staged credential, assembled at runtime (no literal secret-shaped string)
    printf 'app start\n%s = "%s%s"\n' "api_key" "FAKE" "$(printf '0%.0s' $(seq 16))" >> app.py
  )
  : > "$ws/.mentu/ledger.jsonl"   # an EMPTY ledger that EXISTS (verdict keys on it)
  printf '{"active_protocols":[],"auto_review":true,"step_label":"impl"}\n' > "$ws/.claude/protocol-state.json"
  msg='{"last_assistant_message":"done. LOOP_COMPLETE"}'
  ( cd "$ws" && printf '%s' "$msg" | MENTU_STEP_CMT_LEDGER=cmt_e2e MENTU_ACTOR=agent:e2e MENTU_WORKSPACE=ws MENTU_STEP_TIER=2 python3 "$HOOKS/review_gate.py" ) >/dev/null 2>&1
  code=$?
  reopens="$(grep -c '"op": "reopen"' "$ws/.mentu/ledger.jsonl" 2>/dev/null)"; reopens="${reopens:-0}"
  { [ "$code" = 2 ] && [ "$reopens" = 1 ]; } \
    && assert 1 "review_gate blocks (exit 2) + exactly one reopen op on the staged secret" \
    || assert 0 "expected exit2 + 1 reopen (code=$code reopens=$reopens)"
else
  skip "git absent — staged-secret review-gate case"
fi

# ───────── 4. Explicit scope: out-of-scope file → safety validator denies ──────
note "4. Explicit scope: out-of-scope file → validate_safety fails (deny)"
if python3 - <<PYEOF >/dev/null 2>&1
import sys
sys.path.insert(0, "$HOOKS_REPO")
from mentu_policy import gates
class FakeProbe:
    def git_diff_text(self): return ""                       # no secrets
    def git_changed_files(self): return ["docs/out-of-scope.md"]
r = gates.validate_safety(FakeProbe(), scope=["src/"])
ok = (not r.passed) and any("Out of scope" in d for d in r.details)
sys.exit(0 if ok else 1)
PYEOF
then assert 1 "validate_safety(scope=[src/]) flags an out-of-scope file (deny basis)"
else assert 0 "out-of-scope file was not flagged by the safety validator"
fi

# ───────── 5. Context-isolation line boundary: 201 → block, 199 → allow ────────
note "5. Sub-agent message boundary: 201 lines → block (exit 2), 199 → allow"
ws="$ROOT/iso-ws"; mkdir -p "$ws/.claude"
printf '{"active_protocols":["context-isolation"]}\n' > "$ws/.claude/protocol-state.json"
mk_msg(){ python3 -c 'import json,sys;n=int(sys.argv[1]);print(json.dumps({"last_assistant_message":"\n".join("line %d"%i for i in range(n))}))' "$1"; }
( cd "$ws" && mk_msg 201 | python3 "$HOOKS/context_isolation_gate.py" ) >/dev/null 2>&1; c201=$?
( cd "$ws" && mk_msg 199 | python3 "$HOOKS/context_isolation_gate.py" ) >/dev/null 2>&1; c199=$?
{ [ "$c201" = 2 ] && [ "$c199" = 0 ]; } \
  && assert 1 "201-line message blocks (exit 2); 199-line allows (exit 0)" \
  || assert 0 "boundary wrong (201→$c201 want 2; 199→$c199 want 0)"

# ───────── 6. Trust bands via the three fixture ledgers + destructive@.85 ──────
note "6. Trust bands: .90→allow / .50→{} / .33→deny / destructive@.85→{}"
read_stdin='{"tool_name":"Read","tool_input":{}}'
bash_stdin='{"tool_name":"Bash","tool_input":{"command":"rm -rf build/"}}'
mk_ws(){ local w="$ROOT/trust-$1"; mkdir -p "$w/.mentu"; printf '%s' "$w"; }
w=$(mk_ws high); cp "$FIX/ledger_trust_high.jsonl" "$w/.mentu/ledger.jsonl"; run_shim "$w" "$read_stdin" --agent claude --event pre_tool; out_high="$OUT"
w=$(mk_ws mid);  cp "$FIX/ledger_trust_mid.jsonl"  "$w/.mentu/ledger.jsonl"; run_shim "$w" "$read_stdin" --agent claude --event pre_tool; out_mid="$OUT"
w=$(mk_ws low);  cp "$FIX/ledger_trust_low.jsonl"  "$w/.mentu/ledger.jsonl"; run_shim "$w" "$read_stdin" --agent claude --event pre_tool; out_low="$OUT"
w=$(mk_ws dest)
{ i=0; while [ $i -lt 17 ]; do echo '{"op":"approve","payload":{"body":"ok"}}'; i=$((i+1)); done
  i=0; while [ $i -lt 3 ];  do echo '{"kind":"warning","payload":{"body":"w"}}'; i=$((i+1)); done; } > "$w/.mentu/ledger.jsonl"
run_shim "$w" "$bash_stdin" --agent claude --event pre_tool; out_dest="$OUT"
b_ok=1
printf '%s' "$out_high" | grep -q '"permissionDecision": "allow"' || b_ok=0
[ "$(trimws "$out_mid")"  = '{}' ] || b_ok=0
printf '%s' "$out_low"  | grep -q '"permissionDecision": "deny"'  || b_ok=0
[ "$(trimws "$out_dest")" = '{}' ] || b_ok=0
[ "$b_ok" = 1 ] && assert 1 "trust bands resolve correctly (high allow / mid {} / low deny / destructive@.85 {})" \
                || assert 0 "trust bands wrong (high=[$out_high] mid=[$out_mid] low=[$out_low] dest=[$out_dest])"

# ───────── 7. Cursor real verdict (the M3 hardcode → verdict change) ───────────
note "7. Cursor real verdict: clean → {\"continue\":true}; low-trust → {\"continue\":false,…}"
run_shim "$ROOT" '{}' --agent cursor beforeShellExecution; clean="$OUT"
low="$(python3 - <<PYEOF 2>/dev/null
import sys
sys.path.insert(0, "$HOOKS_REPO")
from mentu_policy import evaluate
import mentu_policy.gates  # noqa: F401 — importing self-registers the gate engine
from mentu_policy.adapters import cursor
ev = cursor.decode({}, "beforeShellExecution", {}, ".")
dec = evaluate(ev, {"trust": 0.10})       # affirmative low-trust ledger evidence
out, code = cursor.encode(dec, ev)
sys.stdout.write(out)
PYEOF
)"
c_ok=1
[ "$(trimws "$clean")" = '{"continue":true}' ] || c_ok=0
printf '%s' "$low" | grep -q '"continue":false' || c_ok=0
printf '%s' "$low" | grep -q '"reason"' || c_ok=0
[ "$c_ok" = 1 ] && assert 1 "cursor clean=byte-equal {\"continue\":true}; low-trust={\"continue\":false,reason}" \
              || assert 0 "cursor verdict wrong (clean=[$clean] low=[$low])"

# ───────── 8. Gemini capability honesty: deny → {} + capability_degraded ───────
note "8. Gemini capability honesty: deny → stdout {} AND capability_degraded logged"
: > "$MENTU_STUB_LOG"
gws="$ROOT/gemini-ws"; mkdir -p "$gws"
if ( cd "$gws" && python3 - <<PYEOF >/dev/null 2>&1
import sys, os
sys.path.insert(0, "$HOOKS_REPO")
import mentu_policy.adapters.shim as shim
from mentu_policy.abi import Decision
# Simulate a gate verdict of DENY reaching Gemini (post-hoc, gate:False), then
# run the REAL shim.compute + thunk so the degrade ladder + the fire-and-forget
# capture path execute exactly as in production.
_orig = shim._route
shim._route = lambda event, he, ctx: Decision.deny("synthetic low-trust deny")
try:
    out, code, decision, thunk = shim.compute(
        "gemini", None, '{"hook_event_name":"AfterTool"}', dict(os.environ), os.getcwd())
    thunk()
finally:
    shim._route = _orig
sys.stdout.write(out)
ok = (out.strip() == "{}") and (getattr(decision.verb, "value", "") == "annotate")
sys.exit(0 if ok else 1)
PYEOF
); then deny_ok=1; else deny_ok=0; fi
logged=0; grep -q 'capability_degraded' "$MENTU_STUB_LOG" 2>/dev/null && logged=1
{ [ "$deny_ok" = 1 ] && [ "$logged" = 1 ]; } \
  && assert 1 "gemini deny → {} stdout + capability_degraded captured (never a false block)" \
  || assert 0 "gemini honesty broken (deny→{}+annotate=$deny_ok  capability_degraded-logged=$logged)"

# ───────── 9. Golden replay (behavior-preservation) ────────────────────────────
note "9. Golden replay — capture-golden-vectors.sh --verify all-green"
gv="$(bash "$GOLDEN_SCRIPT" --verify 2>&1)"; gvc=$?
if [ "$gvc" = 0 ] && printf '%s' "$gv" | grep -q "mismatches=0"; then
  assert 1 "golden vectors verify all-green ($(printf '%s' "$gv" | grep -oE 'checked=[0-9]+' | head -1))"
else
  assert 0 "golden verify failed: $(printf '%s' "$gv" | grep -iE 'mismatch|diff' | head -2 | tr '\n' ' ')"
fi

# ───────── 10. Actor-precedence matrix through the shim ────────────────────────
note "10. Actor-precedence matrix (mentu universal hook) + nocolon→agent:unknown"
actor_of(){ # actor_of <env-assignments...> -> resolved actor from the capture argv
  : > "$MENTU_STUB_LOG"
  ( cd "$ROOT" && printf '%s' '{"hook_event_name":"Stop","cwd":"/w/x"}' | env "$@" python3 "$SHIM" --agent mentu ) >/dev/null 2>&1
  python3 -c '
import json,sys
ls=[l for l in open(sys.argv[1]).read().splitlines() if l.strip()]
a=json.loads(ls[-1]) if ls else []
print(a[a.index("--actor")+1] if "--actor" in a else "NONE")
' "$MENTU_STUB_LOG"
}
m_ok=1
[ "$(actor_of MENTU_ACTOR=human:rashid CURSOR_SESSION_ID=cs)" = "human:rashid" ]      || m_ok=0
[ "$(actor_of SUPERSET_TAB_ID=ss CURSOR_SESSION_ID=cs)"       = "agent:superset-hosted" ] || m_ok=0
[ "$(actor_of CURSOR_SESSION_ID=cs CODEX_SESSION_ID=cx)"      = "agent:cursor" ]      || m_ok=0
[ "$(actor_of CODEX_SESSION_ID=cx)"                          = "agent:codex" ]       || m_ok=0
[ "$(actor_of GEMINI_SESSION_ID=gm)"                        = "agent:gemini" ]      || m_ok=0
[ "$(actor_of)"                                             = "agent:claude" ]      || m_ok=0
[ "$(actor_of MENTU_ACTOR=nocolon)"                         = "agent:unknown" ]     || m_ok=0
[ "$m_ok" = 1 ] && assert 1 "actor precedence matrix 7/7 (override→superset→cursor→codex→gemini→claude; nocolon→unknown)" \
                || assert 0 "actor precedence mismatch"

# ───────── 11. Installer idempotency under a dedicated sandbox HOME ────────────
note "11. Installer idempotency (sandbox HOME) + capability-aware wiring"
ihome="$ROOT/inst-home"; mkdir -p "$ihome/.claude" "$ihome/.cursor" "$ihome/.gemini" "$ihome/.codex"
printf '{"hooks":{}}\n' > "$ihome/.claude/settings.json"
HOME="$ihome" bash "$INSTALLER" >/dev/null 2>&1
s1="$(cat "$ihome/.claude/settings.json" "$ihome/.cursor/hooks.json" "$ihome/.gemini/settings.json" "$ihome/.codex/hooks.json" 2>/dev/null | cksum)"
HOME="$ihome" bash "$INSTALLER" >/dev/null 2>&1
s2="$(cat "$ihome/.claude/settings.json" "$ihome/.cursor/hooks.json" "$ihome/.gemini/settings.json" "$ihome/.codex/hooks.json" 2>/dev/null | cksum)"
i_idem=0; { [ "$s1" = "$s2" ] && [ -n "$s1" ]; } && i_idem=1
i_pkg=0;  [ -f "$ihome/.mentu/hooks/mentu_policy/abi.py" ] && i_pkg=1
i_nogate=0; grep -qE 'PreToolUse|PermissionRequest|permission' "$ihome/.gemini/settings.json" || i_nogate=1
i_bak=0;  ls -d "$ihome"/.mentu/backups/*/ >/dev/null 2>&1 && i_bak=1
{ [ "$i_idem" = 1 ] && [ "$i_pkg" = 1 ] && [ "$i_nogate" = 1 ] && [ "$i_bak" = 1 ]; } \
  && assert 1 "installer idempotent + package deployed + gemini gate-free + backups present" \
  || assert 0 "installer e2e wrong (idempotent=$i_idem package=$i_pkg gemini-gate-free=$i_nogate backups=$i_bak)"

# ───────── 12. One core, two agents: same offending event → same deny ──────────
note "12. One core, two agents: agent=claude and agent=mentu → same DENY from evaluate()"
if python3 - <<PYEOF >/dev/null 2>&1
import sys
sys.path.insert(0, "$HOOKS_REPO")
from mentu_policy import evaluate
import mentu_policy.gates  # noqa: F401 — importing self-registers the gate engine
from mentu_policy.abi import AgentEvent, Verb
msg = "\n".join("line %d" % i for i in range(201))   # over the isolation line limit
ctx = {"isolation_active": True}
d_claude = evaluate(AgentEvent(agent="claude", event="subagent_stop", message=msg), ctx)
d_mentu  = evaluate(AgentEvent(agent="mentu",  event="subagent_stop", message=msg), ctx)
# The mentu adapter module lands in M5; the SHARED core verdict is asserted here.
ok = (d_claude.verb == d_mentu.verb == Verb.DENY)
sys.exit(0 if ok else 1)
PYEOF
then assert 1 "evaluate() yields the same DENY for agent=claude and agent=mentu (one core, two agents)"
else assert 0 "the shared core verdict diverged between claude and mentu"
fi

# ───────────────────────── Report ─────────────────────────
note "Result"
printf '  \033[1mPASS=%d  FAIL=%d  SKIP=%d\033[0m\n' "$PASS" "$FAIL" "$SKIP"
if [ "$FAIL" = 0 ]; then
  rm -rf "$ROOT"
  printf '  \033[32mall policy-harness invariants hold\033[0m\n'
  exit 0
else
  printf '  artifacts kept for debugging: %s\n' "$ROOT"
  exit 1
fi
