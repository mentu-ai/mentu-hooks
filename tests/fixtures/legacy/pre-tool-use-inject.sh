#!/usr/bin/env bash
# Hook: pre-tool-use-inject
# Fires: PreToolUse (matcher: Agent)
# Input (stdin JSON): CC base fields + {tool_name, tool_input, tool_use_id}
# Output (stdout JSON): hookSpecificOutput with updatedInput to inject CIR context
# Purpose: Inject CIR ledger context into subagent prompts so they inherit epistemic state
set -euo pipefail

INPUT=$(cat)
TOOL=$(echo "$INPUT" | jq -r '.tool_name // ""')

# Only inject into Agent tool calls (matcher handles this, but belt-and-suspenders)
if [ "$TOOL" != "Agent" ]; then
    echo '{}'
    exit 0
fi

ORIGINAL_PROMPT=$(echo "$INPUT" | jq -r '.tool_input.prompt // ""')

# Skip if no prompt to enrich
if [ -z "$ORIGINAL_PROMPT" ]; then
    echo '{}'
    exit 0
fi

# Build CIR context block from ledger
CIR_CONTEXT=""
LEDGER=".mentu/ledger.jsonl"
if [ -f "$LEDGER" ]; then
    RECENT=$(tail -20 "$LEDGER" | jq -r 'select(.op == "capture" or .op == "annotate") | "\(.op): \(.payload.body // .body // .kind // "unknown")"' 2>/dev/null | tail -10)
    if [ -n "$RECENT" ]; then
        CIR_CONTEXT="

<cir-context>
Recent evidence from the epistemic ledger (read-only — do not modify):
${RECENT}
</cir-context>"
    fi
fi

# Build trust chain summary
TRUST_CONTEXT=""
if [ -f "$LEDGER" ]; then
    TRUST_COUNT=$(grep -c '"op":"approve"' "$LEDGER" 2>/dev/null || echo "0")
    WARN_COUNT=$(grep -c '"kind":"warning"' "$LEDGER" 2>/dev/null || echo "0")
    if [ "$TRUST_COUNT" -gt 0 ] || [ "$WARN_COUNT" -gt 0 ]; then
        TRUST_CONTEXT="
Trust state: ${TRUST_COUNT} approvals, ${WARN_COUNT} warnings in this session."
    fi
fi

# Inject into prompt via updatedInput
if [ -n "$CIR_CONTEXT" ] || [ -n "$TRUST_CONTEXT" ]; then
    ENRICHED="${ORIGINAL_PROMPT}${CIR_CONTEXT}${TRUST_CONTEXT}"
    jq -n --arg prompt "$ENRICHED" \
        '{"hookSpecificOutput":{"hookEventName":"PreToolUse","updatedInput":{"prompt":$prompt}}}'
else
    echo '{}'
fi

exit 0
