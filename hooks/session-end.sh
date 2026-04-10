#!/usr/bin/env bash
# Hook: SessionEnd
# Fires: when CC session terminates (after result, before cleanup)
# Input (stdin JSON): CC base fields + {session_id, duration_ms} + mentu enrichment
# Purpose: CIR capture of session completion with final metrics
# NOTE: CC enforces 1,500ms timeout on SessionEnd hooks — must be fast
set -euo pipefail

INPUT=$(cat)

STEP=$(echo "$INPUT" | jq -r '.step_label // "unknown"')
EXIT_CODE=$(echo "$INPUT" | jq -r '.exit_code // 0')
COST=$(echo "$INPUT" | jq -r '.cost_usd // 0')
DURATION=$(echo "$INPUT" | jq -r '.duration_ms // 0')

# Write CIR annotation to ledger (fast file append — microseconds)
LEDGER=".mentu/ledger.jsonl"
if [ -f "$LEDGER" ]; then
    echo "{\"op\":\"annotate\",\"target\":\"$STEP\",\"body\":\"session.end: exit=$EXIT_CODE cost=\$$COST duration=${DURATION}ms\",\"kind\":\"event\",\"actor\":\"hook:session-end\"}" >> "$LEDGER"
fi

# Return immediately — CC has 1.5s timeout on SessionEnd
echo '{}'

# Fire CIR signal (best-effort, strict timeout to stay within budget)
timeout 1 mentu cir capture \
    --kind session_end \
    --body "session.end: exit=$EXIT_CODE cost=\$$COST duration=${DURATION}ms" \
    --domain "$(basename "$PWD")" \
    --actor "hook:session-end" >/dev/null 2>&1 || true

exit 0
