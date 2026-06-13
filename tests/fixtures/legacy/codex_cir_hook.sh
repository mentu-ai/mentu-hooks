#!/usr/bin/env bash
# Mentu CIR Hook — OpenAI Codex CLI Adapter
# Maps Codex hook events to CIR signals.
# Codex events: task_started, exec_command_begin, _approval_request
set -euo pipefail

ACTOR="agent:codex"

INPUT=""
if [[ ! -t 0 ]]; then
    INPUT=$(cat)
fi

EVENT=$(/usr/bin/python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print(d.get('type', d.get('hook_event_name', d.get('event', 'unknown'))))
except:
    print('unknown')
" <<< "${INPUT:-{}}" 2>/dev/null || echo "unknown")

CWD="${PWD:-unknown}"
DOMAIN=$(basename "$CWD" 2>/dev/null || echo "unknown")

case "$EVENT" in
    task_started|UserPromptSubmit)
        KIND="prompt_submit"
        BODY="codex: task started"
        ;;
    exec_command_begin)
        KIND="command_exec"
        BODY="codex: command execution"
        ;;
    _approval_request|PermissionRequest)
        KIND="permission_gate"
        BODY="codex: approval requested"
        ;;
    Stop)
        KIND="session_stop"
        BODY="codex: session ended"
        ;;
    PostToolUse)
        KIND="tool_use"
        BODY="codex: tool completed"
        ;;
    PostToolUseFailure)
        KIND="tool_failure"
        BODY="codex: tool failed"
        ;;
    *)
        KIND="agent_event"
        BODY="codex: ${EVENT}"
        ;;
esac

timeout 2 mentu cir capture \
    --kind "$KIND" \
    --body "$BODY" \
    --domain "$DOMAIN" \
    --actor "$ACTOR" >/dev/null 2>&1 || true

echo '{}'
exit 0
