#!/bin/bash
# Hook: notification
# Fires: Notification — on CC system notifications
# Input (stdin JSON): base hook input + message, level
# Purpose: Capture warnings and errors in the ledger (skip info — too noisy)

set -euo pipefail
INPUT=$(cat)
STEP_LABEL="unknown"
CURRENT_FILE=".mentu/state/.mentu-current"
if [ -f "$CURRENT_FILE" ]; then
    STEP_LABEL=$(jq -r '.step_label // "unknown"' "$CURRENT_FILE")
fi

LEVEL=$(echo "$INPUT" | jq -r '.level // "info"')
MSG=$(echo "$INPUT" | jq -r '.message // ""')

# Only log warnings and errors (info is noise)
if [ "$LEVEL" = "warning" ] || [ "$LEVEL" = "error" ]; then
    if [ -f ".mentu/ledger.jsonl" ]; then
        echo "{\"op\":\"annotate\",\"target\":\"$STEP_LABEL\",\"body\":\"notification.$LEVEL: $MSG\",\"kind\":\"$LEVEL\",\"actor\":\"hook:notification\"}" >> .mentu/ledger.jsonl
    fi
fi

exit 0
