#!/usr/bin/env bash
# Mentu CIR Hook — Cursor Agent Adapter
# Maps Cursor hook events to CIR signals.
# Cursor hooks: beforeSubmitPrompt, stop, beforeShellExecution, beforeMCPExecution
#
# Cursor protocol: hooks write JSON to stdout for responses.
# beforeShellExecution expects {"continue":true} to auto-approve.
set -euo pipefail

# Cursor passes event type as $1
EVENT="${1:-unknown}"
ACTOR="agent:cursor"

INPUT=""
if [[ ! -t 0 ]]; then
    INPUT=$(cat)
fi

CWD="${PWD:-unknown}"
DOMAIN=$(basename "$CWD" 2>/dev/null || echo "unknown")

case "$EVENT" in
    Start|beforeSubmitPrompt)
        KIND="prompt_submit"
        BODY="cursor: prompt submitted"
        ;;
    Stop|stop)
        KIND="session_stop"
        BODY="cursor: session ended"
        ;;
    PermissionRequest|beforeShellExecution)
        KIND="permission_gate"
        BODY="cursor: shell execution requested"
        # Auto-approve — Cursor expects JSON response on stdout
        echo '{"continue":true}'
        ;;
    beforeMCPExecution)
        KIND="permission_gate"
        BODY="cursor: MCP execution requested"
        echo '{"continue":true}'
        ;;
    *)
        KIND="agent_event"
        BODY="cursor: ${EVENT}"
        ;;
esac

# Emit CIR signal (fire-and-forget)
timeout 2 mentu cir capture \
    --kind "$KIND" \
    --body "$BODY" \
    --domain "$DOMAIN" \
    --actor "$ACTOR" >/dev/null 2>&1 || true

# If we haven't already printed a response, print empty JSON
if [[ "$EVENT" != "PermissionRequest" && "$EVENT" != "beforeShellExecution" && "$EVENT" != "beforeMCPExecution" ]]; then
    echo '{}'
fi

exit 0
