#!/usr/bin/env bash
# Mentu CIR Hook — Gemini CLI Adapter
# Maps Gemini hook events to CIR signals.
# Gemini hooks: BeforeAgent, AfterAgent, AfterTool
set -euo pipefail

ACTOR="agent:gemini"

INPUT=""
if [[ ! -t 0 ]]; then
    INPUT=$(cat)
fi

# Gemini passes event info in JSON stdin
EVENT=$(/usr/bin/python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print(d.get('hook_event_name', d.get('event', 'unknown')))
except:
    print('unknown')
" <<< "${INPUT:-{}}" 2>/dev/null || echo "unknown")

CWD="${PWD:-unknown}"
DOMAIN=$(basename "$CWD" 2>/dev/null || echo "unknown")

case "$EVENT" in
    BeforeAgent)
        KIND="prompt_submit"
        BODY="gemini: agent starting"
        ;;
    AfterAgent)
        KIND="session_stop"
        BODY="gemini: agent completed"
        ;;
    AfterTool)
        KIND="tool_use"
        BODY="gemini: tool completed"
        ;;
    *)
        KIND="agent_event"
        BODY="gemini: ${EVENT}"
        ;;
esac

timeout 2 mentu cir capture \
    --kind "$KIND" \
    --body "$BODY" \
    --domain "$DOMAIN" \
    --actor "$ACTOR" >/dev/null 2>&1 || true

echo '{}'
exit 0
