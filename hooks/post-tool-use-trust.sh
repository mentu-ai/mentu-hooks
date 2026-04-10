#!/bin/bash
# Hook: post-tool-use-trust
# Fires: PostToolUse for all tool calls
# Input (stdin JSON): base hook input + tool_name, tool_result, tool_use_id
# Output (stdout JSON): {hookSpecificOutput:{hookEventName:"PostToolUse",additionalContext:"..."}}
# Purpose: Inject trust score after tool results so agent has trust awareness

set -euo pipefail
INPUT=$(cat)
TOOL=$(echo "$INPUT" | jq -r '.tool_name')

# Skip injection for read-only tools (avoid noise)
case "$TOOL" in
    Read|Glob|Grep|Bash|ToolSearch)
        echo '{}'
        exit 0
        ;;
esac

# Only inject for mutating tools (Edit, Write, Agent, etc.)
TRUST_LINE=""
if [ -f ".mentu/ledger.jsonl" ]; then
    APPROVE_COUNT=$(grep -c '"op":"approve"' .mentu/ledger.jsonl 2>/dev/null || true)
    WARN_COUNT=$(grep -c '"kind":"warning"' .mentu/ledger.jsonl 2>/dev/null || true)
    BLOCK_COUNT=$(grep -c 'BLOCKED' .mentu/ledger.jsonl 2>/dev/null || true)

    if [ "$APPROVE_COUNT" -gt 0 ] || [ "$WARN_COUNT" -gt 0 ] || [ "$BLOCK_COUNT" -gt 0 ]; then
        TRUST_LINE="[trust: ${APPROVE_COUNT} approved, ${WARN_COUNT} warnings, ${BLOCK_COUNT} blocked]"
    fi
fi

if [ -n "$TRUST_LINE" ]; then
    jq -n --arg ctx "$TRUST_LINE" '{hookSpecificOutput:{hookEventName:"PostToolUse",additionalContext:$ctx}}'
else
    echo '{}'
fi
