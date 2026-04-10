#!/usr/bin/env bash
# Hook: pre-tool-use-permission
# Fires: PreToolUse for ALL tool calls (no matcher)
# Input (stdin JSON): { tool_name, tool_input, ... }
# Output (stdout JSON): hookSpecificOutput with permissionDecision based on CIR trust
# Purpose: Trust-driven automatic allow/deny decisions — replaces blanket skip-permissions

set -euo pipefail
INPUT=$(cat)
TOOL=$(echo "$INPUT" | jq -r '.tool_name')
TOOL_INPUT=$(echo "$INPUT" | jq -r '.tool_input // empty')

LEDGER=".mentu/ledger.jsonl"

# Compute trust score from ledger: approve / (approve + warn + block)
# Default to 0.5 (neutral) if no ledger or no relevant entries
TRUST="0.50"
if [ -f "$LEDGER" ]; then
    APPROVE_COUNT=$(grep -c '"op":"approve"' "$LEDGER" 2>/dev/null || true)
    WARN_COUNT=$(grep -c '"kind":"warning"' "$LEDGER" 2>/dev/null || true)
    BLOCK_COUNT=$(grep -c 'BLOCKED' "$LEDGER" 2>/dev/null || true)
    APPROVE_COUNT=${APPROVE_COUNT:-0}
    WARN_COUNT=${WARN_COUNT:-0}
    BLOCK_COUNT=${BLOCK_COUNT:-0}
    TOTAL=$((APPROVE_COUNT + WARN_COUNT + BLOCK_COUNT))
    if [ "$TOTAL" -gt 0 ]; then
        TRUST=$(echo "scale=2; $APPROVE_COUNT / $TOTAL" | bc)
    fi
fi

# Detect destructive tool patterns — these need higher trust to auto-allow
IS_DESTRUCTIVE=false
case "$TOOL" in
    Bash)
        if echo "$TOOL_INPUT" | jq -r '.command // empty' 2>/dev/null | grep -qE '(\brm\b|\bkill\b|\bdrop\b|--force|reset --hard|push --force|clean -f)'; then
            IS_DESTRUCTIVE=true
        fi
        ;;
    Write)
        WRITE_PATH=$(echo "$TOOL_INPUT" | jq -r '.file_path // empty' 2>/dev/null)
        if echo "$WRITE_PATH" | grep -qE '(\.env|credentials|\.ssh|\.gnupg|settings\.json)'; then
            IS_DESTRUCTIVE=true
        fi
        ;;
esac

# Set thresholds
ALLOW_THRESHOLD="0.80"
DENY_THRESHOLD="0.40"
if [ "$IS_DESTRUCTIVE" = true ]; then
    ALLOW_THRESHOLD="0.90"
fi

# Compare trust against thresholds and emit decision
# bc returns 1 for true, 0 for false
if [ "$(echo "$TRUST >= $ALLOW_THRESHOLD" | bc)" -eq 1 ]; then
    jq -n --arg reason "CIR trust above threshold ($TRUST >= $ALLOW_THRESHOLD)" \
        '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"allow",permissionDecisionReason:$reason}}'
elif [ "$(echo "$TRUST < $DENY_THRESHOLD" | bc)" -eq 1 ]; then
    jq -n --arg reason "CIR trust below threshold ($TRUST < $DENY_THRESHOLD)" \
        '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$reason}}'
else
    # Middle range: no decision, fall through to CC default behavior
    echo '{}'
fi
