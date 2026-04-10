#!/bin/bash
# Stop hook: Capture step results for inter-step context + write step status JSON.
# Receives JSON via stdin with last_assistant_message field.
#
# Uses step label as filename (no timestamp). Last write wins — the final
# exit attempt after review gate passes produces the authoritative result.

INPUT=$(cat)

STOP_ACTIVE=$(echo "$INPUT" | jq -r '.stop_hook_active // false')

RESULTS_DIR=".mentu/state/step-results"
mkdir -p "$RESULTS_DIR" 2>/dev/null

# Derive step label from the current sequence tracker
CURRENT_FILE=".mentu/state/.mentu-current"
if [ -f "$CURRENT_FILE" ]; then
  STEP_LABEL=$(jq -r '.step_label // "unknown"' "$CURRENT_FILE")
else
  STEP_LABEL="unknown"
fi

MESSAGE=$(echo "$INPUT" | jq -r '.last_assistant_message // empty')

# Write result using step label only — last invocation overwrites prior attempts.
# This prevents duplicate files from review gate retries.
if [ -n "$MESSAGE" ] && [ "$STEP_LABEL" != "unknown" ]; then
  echo "$MESSAGE" > "$RESULTS_DIR/${STEP_LABEL}.md"
fi

# Step status is written by mentu-engine (which has exit_code and duration).
# This hook only captures the result message.

exit 0
