#!/usr/bin/env bash
# Hook lane: inject (rare, measured)
# Fires: SessionStart
# Purpose: ask CIRReadGateway for one bounded session-start brief. No raw
# `mentu cir query`, no pattern dumps, no ledger tail injection.
set -euo pipefail

INPUT=$(cat)

eval "$(/usr/bin/python3 -c '
import json, shlex, sys
try:
    d = json.load(sys.stdin)
except Exception:
    d = {}
print("SESSION_ID=" + shlex.quote(str(d.get("session_id", ""))))
print("CWD=" + shlex.quote(str(d.get("cwd", ""))))
' <<< "$INPUT" 2>/dev/null)" || { SESSION_ID=""; CWD=""; }

DOMAIN=$(basename "${CWD:-$PWD}" 2>/dev/null || echo "unknown")
MARKER_DIR="$HOME/.mentu/.cir-injected"
MARKER_FILE="$MARKER_DIR/${SESSION_ID:-unknown}"
BUDGET=600
if [[ -f "$MARKER_FILE" ]]; then
    BUDGET=300
fi

BRIEF_JSON=$(timeout 10 env MENTU_DISABLE_CIR_READ_SIGNALS=1 mentu cir brief \
    --surface session_start \
    --intent "session start for ${DOMAIN}" \
    --domain "$DOMAIN" \
    --budget "$BUDGET" \
    --format json 2>/dev/null || echo '{}')

SYSTEM_MESSAGE=$(/usr/bin/python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("")
    raise SystemExit(0)
print((d.get("system_message") or "").strip())
' <<< "$BRIEF_JSON" 2>/dev/null || true)

mkdir -p "$MARKER_DIR"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$MARKER_FILE"

if [[ -z "$SYSTEM_MESSAGE" ]]; then
    echo '{}'
    exit 0
fi

/usr/bin/python3 -c '
import json, sys
ctx = sys.stdin.read()
print(json.dumps({"systemMessage": ctx}))
' <<< "$SYSTEM_MESSAGE"
