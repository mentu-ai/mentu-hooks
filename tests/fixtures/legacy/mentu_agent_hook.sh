#!/usr/bin/env bash
# Mentu Universal Agent Hook → CIR Signal Bridge
# Normalizes events from ANY AI tool (Claude, Cursor, Codex, Gemini)
# into CIR epistemic signals with per-actor attribution.
#
# Input: JSON on stdin (hook_event_name, tool_name, session_id, cwd, etc.)
# Output: JSON on stdout (hookSpecificOutput for the calling tool)
#
# Design: fire-and-forget. Never blocks the tool. Always exits 0.
set -euo pipefail

INPUT=$(cat)

# --- Parse hook input via python (no eval, no jq dependency) ---
read -r EVENT TOOL_NAME SESSION_ID CWD < <(/usr/bin/python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
except (json.JSONDecodeError, ValueError):
    print('unknown unknown unknown unknown')
    sys.exit(0)

event = d.get('hook_event_name', d.get('event', 'unknown'))
tool = d.get('tool_name', '')
sid = d.get('session_id', 'unknown')
cwd = d.get('cwd', '')
print(f'{event}\t{tool}\t{sid}\t{cwd}')
" <<< "$INPUT" 2>/dev/null || echo "unknown	unknown	unknown	unknown")

DOMAIN=$(basename "$CWD" 2>/dev/null || echo "unknown")

# --- Actor detection (priority order) ---
if [[ -n "${MENTU_ACTOR:-}" ]]; then
    ACTOR="$MENTU_ACTOR"
elif [[ -n "${SUPERSET_TAB_ID:-}" ]]; then
    ACTOR="agent:superset-hosted"
elif [[ -n "${CURSOR_SESSION_ID:-}" ]]; then
    ACTOR="agent:cursor"
elif [[ -n "${CODEX_SESSION_ID:-}" ]]; then
    ACTOR="agent:codex"
elif [[ -n "${GEMINI_SESSION_ID:-}" ]]; then
    ACTOR="agent:gemini"
else
    ACTOR="agent:claude"
fi

# Validate actor format: must contain ':'
if [[ "$ACTOR" != *":"* ]]; then
    ACTOR="agent:unknown"
fi

# --- Map event → CIR signal kind + body ---
case "$EVENT" in
    UserPromptSubmit|BeforeAgent)
        KIND="prompt_submit"
        BODY="prompt submitted (${TOOL_NAME:-session})"
        ;;
    PostToolUse)
        case "$TOOL_NAME" in
            Edit|Write|MultiEdit)
                # file_change already emitted by post-tool-use.sh — skip duplicate
                echo '{}'
                exit 0
                ;;
            Bash)
                KIND="command_exec"
                BODY="bash command executed"
                ;;
            Agent)
                KIND="agent_spawn"
                BODY="sub-agent spawned"
                ;;
            Read|Glob|Grep)
                # Read-only tools — low signal, skip to reduce noise
                echo '{}'
                exit 0
                ;;
            *)
                KIND="tool_use"
                BODY="tool: ${TOOL_NAME}"
                ;;
        esac
        ;;
    PostToolUseFailure)
        KIND="tool_failure"
        BODY="FAILED: ${TOOL_NAME}"
        ;;
    Stop|AfterAgent)
        KIND="session_stop"
        BODY="session ended"
        ;;
    PermissionRequest)
        KIND="permission_gate"
        BODY="permission requested: ${TOOL_NAME}"
        ;;
    AfterTool)
        KIND="tool_use"
        BODY="tool completed (${TOOL_NAME:-unknown})"
        ;;
    *)
        KIND="agent_event"
        BODY="event: ${EVENT}"
        ;;
esac

# --- Return success immediately (before CIR capture) ---
echo '{}'

# --- Emit CIR signal (after stdout, with timeout to stay within 3s) ---
timeout 2 mentu cir capture \
    --kind "$KIND" \
    --body "${ACTOR}: ${BODY}" \
    --domain "$DOMAIN" \
    --actor "$ACTOR" >/dev/null 2>&1 || true

exit 0
