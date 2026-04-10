#!/bin/bash
# Hook: teammate-idle
# Fires: TeammateIdle — when subagent has no output for 60+ seconds
# Input (stdin JSON): base hook input + agent_id, agent_name
# Purpose: Early warning for stuck subagents — log to ledger before idle timeout kills

set -euo pipefail
INPUT=$(cat)
STEP_LABEL="unknown"
CURRENT_FILE=".mentu/state/.mentu-current"
if [ -f "$CURRENT_FILE" ]; then
    STEP_LABEL=$(jq -r '.step_label // "unknown"' "$CURRENT_FILE")
fi

AGENT_NAME=$(echo "$INPUT" | jq -r '.agent_name // "unknown"')
AGENT_ID=$(echo "$INPUT" | jq -r '.agent_id // "unknown"')

if [ -f ".mentu/ledger.jsonl" ]; then
    echo "{\"op\":\"annotate\",\"target\":\"$STEP_LABEL\",\"body\":\"teammate.idle: $AGENT_NAME ($AGENT_ID)\",\"kind\":\"warning\",\"actor\":\"hook:teammate-idle\"}" >> .mentu/ledger.jsonl
fi

exit 0
