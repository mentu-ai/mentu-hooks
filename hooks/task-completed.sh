#!/bin/bash
# Hook: task-completed
# Fires: TaskCompleted — when CC marks an internal task as completed
# Input (stdin JSON): base hook input + task_id, result
# Purpose: Track granular task progress within a step

set -euo pipefail
INPUT=$(cat)
STEP_LABEL="unknown"
CURRENT_FILE=".mentu/state/.mentu-current"
if [ -f "$CURRENT_FILE" ]; then
    STEP_LABEL=$(jq -r '.step_label // "unknown"' "$CURRENT_FILE")
fi

TASK_ID=$(echo "$INPUT" | jq -r '.task_id // "unknown"')

if [ -f ".mentu/ledger.jsonl" ]; then
    echo "{\"op\":\"annotate\",\"target\":\"$STEP_LABEL\",\"body\":\"task.completed: $TASK_ID\",\"kind\":\"event\",\"actor\":\"hook:task-completed\"}" >> .mentu/ledger.jsonl
fi

exit 0
